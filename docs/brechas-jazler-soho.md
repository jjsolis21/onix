# ONIX FM frente a Jazler SoHo — qué falta y en qué orden

Fuente: `docs/referencia/Manual-JazlerSOHO-en.pdf` (manual oficial, 161 páginas).
Estado de ONIX: rama `fase-0-1-motor-z3bhab`, después de las Fases 0 a 3.

Leyenda: ✅ ya está · 🟡 parcial · ❌ falta

## 1. Programación musical (manual cap. 6.2, 6.3, 6.6) — el corazón de Jazler

| Jazler SoHo | ONIX hoy |
|---|---|
| **Relojes**: patrón de la hora por filas (categoría, jingle, sweeper…) que se repite hasta cambiar de reloj | ✅ |
| **Grilla de relojes 24 × 7** (un reloj por hora y día) | ✅ |
| **Auto SOHO**: carga en el Studio lo que sonará las próximas 2 horas, con hora exacta de cada ítem | ✅ |
| **Compensación**: rellena con un reloj o categoría de respaldo para que la tanda caiga a su hora | ✅ |
| **Force clocks**: un reloj que suena una sola vez en una fecha y hora | ✅ eventos a hora fija (HH:MM, días, vigencia, en punto) |
| **Final Log**: genera el día completo, se revisa, se edita y se emite tal cual | ❌ |
| Separación de artista (minutos) | ✅ |
| Separación de la misma canción al día siguiente en la misma franja | ✅ |
| Aleatoriedad configurable (0 = estricto, todas suenan antes de repetir) | ✅ |
| Reglas restrictivas (p. ej. "pop no seguido de balada") | 🟡 solo "no más de N con la misma voz" |
| Registro de por qué se eligió cada canción | ❌ |

## 2. Tandas comerciales (cap. 5.2)

| Jazler SoHo | ONIX hoy |
|---|---|
| Tandas a hora fija (por defecto :00 y :30) | ✅ (`tandas_minutos`) |
| **Inicio inmediato** por tanda: corta la canción con fade y sale a la hora exacta | ✅ (`tandas_inmediatas`, en automático) |
| **Prioridades** dentro de la tanda: ID → cortina de entrada → hora → cuñas → clima → noticias → cortina de salida | ✅ secciones editables (falta la hora locutada) |
| **Reproducción opcional**: la cortina de entrada no sale si no hay cuñas | ✅ |
| Campañas con varios audios (en orden, al azar o elegido) | ❌ una pauta = un audio |
| Programación semanal o detallada día por día | 🟡 matriz semanal |
| Reportes de emisión real por campaña | ✅ |
| Aviso si la tanda excede su duración máxima | ✅ |

## 3. Eventos, programas y piezas especiales (cap. 5.4, 5.5, 5.7, 5.9, 6.1, 6.4)

| Jazler SoHo | ONIX hoy |
|---|---|
| Programas pregrabados / playlists a hora fija, repetidos (con inicio inmediato) | ✅ con eventos a hora fija (reloj con el audio fijo) |
| Archivo de "largo variable": se reemplaza el MP3 con el mismo nombre cada semana | ❌ |
| **Hora locutada** (Stitcher): apertura + hora + minutos pregrabados + cierre | ❌ |
| Avance de próximas canciones con sus hooks | ❌ |
| Noticias con cama musical ajustada al largo de la voz | ❌ |
| **Sweepers sobre la canción**: al inicio, antes del fin del intro, antes del final, puente | ❌ (solo como pista suelta) |
| **Voice tracking** grabado sobre la lista | ❌ |
| Retransmisión de otra fuente (entrada de línea o stream) con fuente de respaldo | ❌ |
| Memos (notas para el locutor) | ❌ |

## 4. Estudio y salidas (cap. 4, 5.6, 7.1)

| Jazler SoHo | ONIX hoy |
|---|---|
| Salidas: principal, monitor y **cartuchera por salida propia** | ✅ |
| Modos: automático, manual, retransmisión, fuera de línea | 🟡 automático y manual |
| Cartuchera (instant jingles) por paletas | ✅ (2 bancos) |
| Atajos de teclado configurables | 🟡 fijos (no configurables) |
| Segunda pantalla | 🟡 (una segunda ventana del navegador sirve) |
| Iniciar con Windows y arrancar en automático | ❌ (va con el instalador) |
| Normalización | ✅ |
| Procesamiento VST/DSP | ❌ (bajo prioridad: lo hace el procesador externo) |

## 5. Salida al mundo (cap. 6.5, 8.1, 8.2)

| Jazler SoHo | ONIX hoy |
|---|---|
| Codificadores de streaming hacia Icecast/Shoutcast | ❌ |
| Metadatos "sonando ahora" a la web, TuneIn, HTTP/TCP y dentro del stream | ❌ |
| RDS para FM | ❌ |
| Clima por API para RDS | ❌ |
| Usuarios y permisos | ❌ |

## Orden propuesto

1. **Relojes** + grilla 24×7 + próximas 2 horas visibles y editables en el Studio + compensación
   para llegar a la tanda a tiempo. Incluye jingles y sweepers como filas del reloj, separación
   de canción por día y aleatoriedad configurable. *Sin esto ONIX es un reproductor con pautas.*
2. **Tandas al estilo Jazler**: prioridades (ID, cortina de entrada opcional, hora, cuñas, cierre),
   inicio inmediato por tanda y campañas con varios audios.
3. **Eventos**: programas pregrabados a hora fija, force clocks, hora locutada y retransmisión.
4. **Final Log** del día y voice tracking básico.
5. **Streaming y metadatos** (Icecast, web) y RDS si hay transmisor FM.
6. **Usuarios, respaldos, instalador**, inicio con Windows y atajos.
