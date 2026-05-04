# Reporte de Debugging y Stress Testing — Agente Rosa 🩺

## Resumen del proceso
Se ha sometido al núcleo de Rosa (`process_message`) a una batería de pruebas automáticas y manuales simuladas para intentar romper su flujo lógico y detectar inconsistencias.

## 🧪 Casos de Prueba Ejecutados

| ID | Escenario de Stress | Estado | Hallazgo |
|----|----------------------|--------|----------|
| ST-01 | Inputs vacíos o símbolos (`"???"`, `""`) | ✅ Filtro OK | Lo trata como drift o ignora según contexto. |
| ST-02 | Edades extremas o inválidas | ✅ Validado | Ahora maneja 0 y limita a 110. |
| ST-03 | Cambio de intención a mitad de flujo | ✅ Reconducido | Detecta si pides otro seguro y actualiza el slot `product_interest`. |
| ST-04 | Intento de hablar de "coches" en medio de Salud | ✅ Bloqueado | La intercepción de `is_out_of_scope_product` es robusta. |
| ST-05 | Respuestas repetitivas | ✅ Dedup OK | El motor de deduplicación evita bucles. |
| ST-06 | Presupuesto en texto libre incoherente | ✅ OK | Ahora se captura como texto si no hay número, permitiendo lectura humana en WA. |

## 🛠️ Errores detectados y parches aplicados

1. **Error de Tipo en `wa_sent_at` (Crítico):** Se estaba asignando el string `"pending_db"` a una columna de tipo `TIMESTAMPTZ`, lo que causaría un crash en Postgres.
   - *Fix:* Ahora se asigna `datetime.datetime.now()` correctamente.
2. **Limitación en Extractores de Producto:** No reconocía "el primero" o "la segunda" como opciones válidas en el paso inicial.
   - *Fix:* Añadida lógica ordinal a `_extract_product`.
3. **Rigidez en `num_people`:** Usuarios diciendo "nosotros dos" o "mi pareja y yo" no siempre activaban el slot.
   - *Fix:* Ampliada la base de regex para variaciones naturales.
4. **Falta de Variedad en A/B Testing:** Ambas versiones compartían las mismas frases de storytelling.
   - *Fix:* Creado set diferenciado `_STORYTELLING_HINTS_B` para una voz más cálida y personal en la versión B.

## 🔬 Observaciones de Lógica
- **Resiliencia:** El agente es muy resistente a salir del flujo de seguros gracias al umbral de 2 turnos de drift.
- **RAG:** Las preguntas de producto interrumpen correctamente y Rosa retoma el hilo.
