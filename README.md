# NotesAi
App para apuntes gratis.

PWA que graba una clase, la transcribe por bloques con la API de Google Gemini y genera apuntes, tarjetas y preguntas de examen. Todo se guarda en el móvil (IndexedDB); el audio solo sale del dispositivo al pulsar «Generar apuntes».

## Agente de clipping automático

Al terminar los apuntes, un agente recorta solo los momentos de la clase que merece la pena volver a escuchar. Funciona en tres pasos, todos en `index.html`:

1. **Plan.** `clipsPrompt` manda a Gemini la transcripción dividida en bloques con su minuto de inicio y fin y pide, con salida JSON (`CLIPS_SCHEMA`), entre 3 y N momentos clave: avisos de examen, definiciones, fórmulas, ejemplos, tareas, respuestas a dudas y el cierre de la clase. Cada momento trae una **cita literal** de la transcripción, un título, el porqué y una importancia de 1 a 5. Los minutos que el alumno marcó con ★ durante la grabación siempre reciben un clip.
2. **Localización.** `locateQuote` busca la cita dentro del texto del bloque (normalizando tildes y puntuación, y probando con trozos si la IA corrigió algo) y `clipWindow` convierte esa posición en un tramo de tiempo con margen: entre 15 y 90 segundos. Si la cita no aparece, el clip se marca como aproximado (≈).
3. **Recorte.** `runClipAgent` decodifica el bloque de audio, corta el tramo como WAV mono a 16 kHz y lo guarda en el almacén `clips` de IndexedDB, antes de que la app borre el audio original de la clase. Los clips que se pisan se funden quedándose el más importante.

En la clase aparece la pestaña **✂ Clips**: cada clip se puede escuchar, compartir como archivo `.wav` o copiar como lista de texto. Los clips también se incluyen al copiar o compartir los apuntes en Markdown. Desde el menú ⋯ de la clase se pueden volver a recortar; si el audio ya se borró, los clips salen solo con texto y minuto.

En **Ajustes** se puede apagar el clipping automático y elegir cuántos clips como máximo (5, 8 o 12).
