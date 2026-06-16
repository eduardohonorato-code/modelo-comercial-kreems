/**
 * obuma-webhook — Supabase Edge Function (Deno)
 *
 * Recibe el webhook de Obuma cuando se emite un nuevo documento (factura o NC)
 * y hace upsert idempotente en dim_cliente, dim_producto y fact_ventas,
 * replicando la lógica del ETL Python (etl/loaders/obuma.py + etl/cleaners.py).
 *
 * Secrets requeridos (supabase secrets set …):
 *   OBUMA_WEBHOOK_SECRET   — token compartido para validar que el POST viene de Obuma
 *
 * Supabase inyecta automáticamente:
 *   SUPABASE_URL              — URL del proyecto
 *   SUPABASE_SERVICE_ROLE_KEY — llave con bypass RLS (nunca exponer al front)
 */

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// ─── Tipos ────────────────────────────────────────────────────────────────────

/** Payload que Obuma envía en el webhook de "nuevo documento". */
interface ObumaPayload {
  evento: string;        // "documento.emitido" | "nota_credito.emitida" | ...
  sociedad: string;      // "Gran Natural SPA" | "Acuña" | "GRANNATURAL" | ...
  documento: {
    tipo: string;        // "FACTURA ELECTRONICA" | "NOTA DE CREDITO ELECTRONICA"
    numero: string;      // N° DCTO — llave de cruce con Autoventa
    fecha: string;       // "DD-MM-YYYY" o "YYYY-MM-DD"
    sucursal: string;
    vendedor: string;    // nombre libre, se normaliza para lookup en dim_vendedor
    cliente_rut: string;
    cliente_razon_social: string;
    cliente_comuna?: string;
    cliente_region?: string;
    cliente_tipo?: string;
    items: ObumaItem[];
  };
}

interface ObumaItem {
  codigo: string;
  nombre: string;
  categoria?: string;
  subcategoria?: string;
  fabricante?: string;
  unidad_medida?: string;
  cantidad: number;
  subtotal_neto: number;
  total: number;
  costo?: number;
  margen?: number;
}

// ─── Constantes de negocio (sección 3 del CLAUDE.md) ─────────────────────────

const TIPO_DCTO_NEGATIVO = new Set([
  "NOTA DE CREDITO ELECTRONICA",
  "NOTA DE CREDITO",
]);

const SOCIEDAD_ID: Record<string, number> = {
  // Variantes posibles del nombre de sociedad que puede enviar Obuma
  "ACUNA":       1,
  "ACUÑA":       1,
  "GRANNATURAL": 2,
  "GRAN NATURAL": 2,
  "GRAN NATURAL SPA": 2,
};

// ─── Helpers de limpieza (puerto de etl/cleaners.py) ─────────────────────────

/**
 * Quita acentos, colapsa espacios, convierte a mayúsculas.
 * Espejo de _normalizar_nombre() en cleaners.py.
 */
function normalizarNombre(nombre: string): string {
  return nombre
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toUpperCase()
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Determina el sociedad_id desde el campo "sociedad" del payload.
 * Tolerante a variaciones de mayúsculas/acentos.
 */
function mapearSociedad(sociedad: string): number {
  const s = normalizarNombre(sociedad);
  for (const [clave, id] of Object.entries(SOCIEDAD_ID)) {
    if (s.includes(normalizarNombre(clave))) return id;
  }
  // Fallback: si el nombre no coincide, loguear en el detalle y usar Gran Natural
  console.warn(`[obuma-webhook] Sociedad desconocida: "${sociedad}". Asignando Gran Natural (id=2).`);
  return 2;
}

/**
 * Normaliza RUT chileno a formato XX.XXX.XXX-X.
 * Espejo de normalizar_rut() en cleaners.py.
 */
function normalizarRut(raw: string): string | null {
  if (!raw) return null;
  const s = raw.replace(/[.\-\s]/g, "").toUpperCase().trim();
  if (s.length < 2) return null;
  const cuerpo = s.slice(0, -1);
  const dv = s.slice(-1);
  if (!/^\d+$/.test(cuerpo)) return null;
  const num = parseInt(cuerpo, 10);
  if (isNaN(num)) return null;
  // Separar miles con punto (formato chileno)
  const cuerpoFmt = num.toLocaleString("es-CL").replace(/,/g, ".");
  return `${cuerpoFmt}-${dv}`;
}

/**
 * Parsea fechas en los formatos que usa Obuma: DD-MM-YYYY y YYYY-MM-DD.
 * Devuelve string ISO (YYYY-MM-DD) para Postgres.
 */
function parsearFecha(raw: string): string | null {
  if (!raw) return null;
  // DD-MM-YYYY
  const m1 = raw.match(/^(\d{2})-(\d{2})-(\d{4})$/);
  if (m1) return `${m1[3]}-${m1[2]}-${m1[1]}`;
  // YYYY-MM-DD  (ya está en formato ISO)
  if (/^\d{4}-\d{2}-\d{2}/.test(raw)) return raw.slice(0, 10);
  return null;
}

/**
 * Aplica signo negativo a montos de NC (regla de negocio sección 3).
 */
function signoMonto(valor: number, tipoDcto: string): number {
  return TIPO_DCTO_NEGATIVO.has(tipoDcto.toUpperCase()) ? -Math.abs(valor) : valor;
}

// ─── Lookup de vendedor en dim_vendedor ───────────────────────────────────────

/**
 * Busca el vendedor_id en la caché de dim_vendedor.
 * Tolerante a variaciones de acento/mayúsculas (mismo criterio que el ETL Python).
 */
function lookupVendedorId(
  nombreRaw: string,
  mapeo: Map<string, number>,
): number | null {
  const clave = normalizarNombre(nombreRaw || "");
  return mapeo.get(clave) ?? null;
}

/** Construye el mapeo {nombre_normalizado → id} desde filas de dim_vendedor. */
function construirMapeo(rows: Array<{ id: number; nombre_canonico: string }>): Map<string, number> {
  const m = new Map<string, number>();
  for (const r of rows) {
    if (r.nombre_canonico && r.id) {
      m.set(normalizarNombre(r.nombre_canonico), r.id);
    }
  }
  return m;
}

// ─── Validación del secret ────────────────────────────────────────────────────

/**
 * Verifica que el request incluya el secret configurado en Supabase.
 * Obuma puede enviarlo como:
 *   - Header "Authorization: Bearer <secret>"
 *   - Header "X-Webhook-Secret: <secret>"
 * Configura el que soporte Obuma al registrar el webhook.
 */
function validarSecret(req: Request): boolean {
  const secret = Deno.env.get("OBUMA_WEBHOOK_SECRET");
  if (!secret) {
    console.warn("[obuma-webhook] OBUMA_WEBHOOK_SECRET no configurado — endpoint desprotegido");
    return true;
  }
  // Header Authorization: Bearer <secret>
  const auth = req.headers.get("authorization") ?? "";
  const bearer = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (bearer === secret) return true;
  // Header X-Webhook-Secret: <secret>
  if ((req.headers.get("x-webhook-secret") ?? "") === secret) return true;
  // Query param ?secret=<secret>  — para clientes como Obuma que no soportan headers custom
  const url = new URL(req.url);
  if (url.searchParams.get("secret") === secret) return true;
  return false;
}

// ─── Handler principal ────────────────────────────────────────────────────────

Deno.serve(async (req: Request): Promise<Response> => {
  // Solo aceptar POST
  if (req.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405 });
  }

  // Leer payload crudo antes de cualquier procesamiento (para el log)
  const bodyText = await req.text();
  let payload: ObumaPayload | null = null;
  let payloadJson: unknown = null;

  try {
    payloadJson = JSON.parse(bodyText);
    payload = payloadJson as ObumaPayload;
  } catch {
    return logAndRespond(null, "error", "Payload no es JSON válido", payloadJson, 400);
  }

  // Verificar secret
  if (!validarSecret(req)) {
    return logAndRespond(payload, "ignorado", "Secret inválido — request rechazado", payloadJson, 401);
  }

  // Validar campos mínimos
  if (!payload.documento?.numero || !payload.documento?.tipo || !payload.documento?.items?.length) {
    return logAndRespond(payload, "error", "Payload incompleto: falta numero, tipo o items", payloadJson, 400);
  }

  // Ignorar eventos que no son documentos de venta
  const eventoNorm = normalizarNombre(payload.evento ?? "");
  if (eventoNorm && !eventoNorm.includes("DOCUMENTO") && !eventoNorm.includes("NOTA")) {
    return logAndRespond(payload, "ignorado", `Evento '${payload.evento}' no requiere procesamiento`, payloadJson, 200);
  }

  // Crear cliente Supabase con service_role (bypasa RLS)
  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );

  try {
    const detalle = await procesarDocumento(supabase, payload);
    return logAndRespond(payload, "ok", detalle, payloadJson, 200, supabase);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[obuma-webhook] Error procesando documento:", msg);
    await escribirLog(supabase, payload, "error", msg, payloadJson);
    return new Response(JSON.stringify({ ok: false, error: msg }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
});

// ─── Procesamiento del documento ──────────────────────────────────────────────

async function procesarDocumento(
  supabase: ReturnType<typeof createClient>,
  payload: ObumaPayload,
): Promise<string> {
  const doc = payload.documento;
  const tipoDcto = (doc.tipo ?? "").toUpperCase().trim();
  const nDcto = String(doc.numero).trim();
  const fecha = parsearFecha(doc.fecha);
  const sociedadId = mapearSociedad(payload.sociedad ?? "");

  if (!fecha) {
    throw new Error(`Fecha inválida: "${doc.fecha}"`);
  }

  // 1. Cargar mapeo de vendedores desde dim_vendedor
  const { data: vendedorRows, error: vErr } = await supabase
    .from("dim_vendedor")
    .select("id, nombre_canonico");
  if (vErr) throw new Error(`Error leyendo dim_vendedor: ${vErr.message}`);
  const mapeoVendedor = construirMapeo(vendedorRows ?? []);

  // 2. Buscar vendedor 'Sin asignar' como fallback
  const { data: sinAsignarRows } = await supabase
    .from("dim_vendedor")
    .select("id")
    .eq("nombre_canonico", "Sin asignar")
    .limit(1);
  const fallbackId: number | null = sinAsignarRows?.[0]?.id ?? null;

  // 3. Resolver vendedor_id
  let vendedorId = lookupVendedorId(doc.vendedor, mapeoVendedor);
  if (vendedorId === null) {
    if (doc.vendedor?.trim()) {
      console.warn(`[obuma-webhook] Vendedor no mapeado: "${doc.vendedor}". Usando fallback.`);
    }
    vendedorId = fallbackId;
  }

  // 4. Normalizar RUT del cliente
  const clienteRut = normalizarRut(doc.cliente_rut);
  if (!clienteRut) {
    throw new Error(`RUT inválido: "${doc.cliente_rut}"`);
  }

  // 5. Upsert dim_cliente
  const clienteRow = {
    rut:          clienteRut,
    razon_social: doc.cliente_razon_social ?? null,
    comuna:       doc.cliente_comuna ?? null,
    region:       doc.cliente_region ?? null,
    tipo:         doc.cliente_tipo ?? null,
    sociedad_id:  sociedadId,
    es_maquina:   false,
  };
  const { error: cErr } = await supabase
    .from("dim_cliente")
    .upsert(clienteRow, { onConflict: "rut" });
  if (cErr) throw new Error(`Upsert dim_cliente: ${cErr.message}`);

  // 6. Upsert dim_producto y fact_ventas por cada línea
  const productosUpsert: object[] = [];
  const ventasUpsert: object[] = [];

  for (let i = 0; i < doc.items.length; i++) {
    const item = doc.items[i];
    const linea = i + 1;

    // dim_producto
    productosUpsert.push({
      codigo:        String(item.codigo).trim(),
      nombre:        item.nombre ?? null,
      categoria:     item.categoria ?? null,
      subcategoria:  item.subcategoria ?? null,
      fabricante:    item.fabricante ?? null,
      unidad_medida: item.unidad_medida ?? null,
    });

    // fact_ventas con signo correcto en montos de NC
    ventasUpsert.push({
      fecha:            fecha,
      tipo_dcto:        tipoDcto,
      n_dcto:           nDcto,
      linea:            linea,
      vendedor_id:      vendedorId,
      cliente_rut:      clienteRut,
      producto_codigo:  String(item.codigo).trim(),
      sociedad_id:      sociedadId,
      sucursal:         doc.sucursal ?? null,
      cantidad:         item.cantidad ?? null,
      neto:             signoMonto(item.subtotal_neto ?? 0, tipoDcto),
      total:            signoMonto(item.total ?? 0, tipoDcto),
      costo:            item.costo != null ? signoMonto(item.costo, tipoDcto) : null,
      margen:           item.margen != null ? signoMonto(item.margen, tipoDcto) : null,
    });
  }

  // Productos primero (FK en fact_ventas)
  const { error: pErr } = await supabase
    .from("dim_producto")
    .upsert(productosUpsert, { onConflict: "codigo" });
  if (pErr) throw new Error(`Upsert dim_producto: ${pErr.message}`);

  const { error: fErr } = await supabase
    .from("fact_ventas")
    .upsert(ventasUpsert, { onConflict: "sociedad_id,tipo_dcto,n_dcto,producto_codigo,linea" });
  if (fErr) throw new Error(`Upsert fact_ventas: ${fErr.message}`);

  return `${tipoDcto} N°${nDcto} | ${doc.items.length} línea(s) | vendedor_id=${vendedorId} | rut=${clienteRut}`;
}

// ─── Log helper ───────────────────────────────────────────────────────────────

async function escribirLog(
  supabase: ReturnType<typeof createClient>,
  payload: ObumaPayload | null,
  status: string,
  detalle: string,
  payloadRaw: unknown,
): Promise<void> {
  try {
    await supabase.from("webhook_log").insert({
      evento:      payload?.evento ?? null,
      n_dcto:      payload?.documento?.numero ?? null,
      sociedad:    payload?.sociedad ?? null,
      status:      status,
      detalle:     detalle,
      payload_raw: payloadRaw,
    });
  } catch (logErr) {
    // No dejar que un error de log enmascare el error original
    console.error("[obuma-webhook] Error escribiendo webhook_log:", logErr);
  }
}

async function logAndRespond(
  payload: ObumaPayload | null,
  status: string,
  detalle: string,
  payloadRaw: unknown,
  httpStatus: number,
  supabase?: ReturnType<typeof createClient>,
): Promise<Response> {
  if (supabase) {
    await escribirLog(supabase, payload, status, detalle, payloadRaw);
  }
  const ok = status === "ok" || status === "ignorado";
  return new Response(
    JSON.stringify({ ok, status, detalle }),
    { status: httpStatus, headers: { "Content-Type": "application/json" } },
  );
}
