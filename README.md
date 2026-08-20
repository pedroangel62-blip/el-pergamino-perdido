# El Pergamino Perdido

Aplicación web para preparar Reels documentales de El Pergamino Perdido con control humano de guion, voz, material visual y publicación.

## Flujo actual

1. El índice maestro recomienda el siguiente tema disponible y no utilizado.
2. El usuario confirma el tema antes de consumir créditos.
3. La aplicación genera y guarda guion, plan visual y textos de publicación.
4. ElevenLabs genera la narración y sus marcas temporales desde el guion aprobado.
5. El usuario escucha y aprueba la voz; hasta entonces las imágenes permanecen bloqueadas.
6. Se buscan fotografías reales antes de ofrecer una recreación con IA.
7. Las ocho imágenes definitivas se confirman antes del montaje.
8. La aplicación sincroniza las ocho imágenes del caso con las frases y marcas reales de la voz, con portada fija de 3 segundos y sin subtítulos.
9. El usuario carga y aprueba la música antes de mezclarla con la voz.
10. FFmpeg añade la Imagen 9 maestra durante 3 segundos: zoom suave, voz ya terminada y fundido final de la música.
11. FFmpeg genera un borrador vertical 1080×1920 a 30 fps sin subtítulos y verifica fotogramas, transiciones, zoom, márgenes, duración y pista de audio.
12. El borrador aprobado se convierte en vídeo final y paquete ZIP descargable.
13. Cada Pergamino se conserva en su propia carpeta con `proyecto.json` como fuente de verdad.

## Índice maestro

El catálogo vive en `backend/data/indice_temas.json` e incluye:

- el banco histórico de temas, normalizado y sin el duplicado del Proyecto Filadelfia;
- la categoría «Crónica negra española — Archivo El Caso»;
- estados de verificación y bloqueo;
- prioridad, sensibilidad, fuentes y ficha documental cuando están disponibles.

La interfaz marca los temas ya usados a partir de los proyectos existentes. Los expedientes de El Caso con evidencia A o B pueden seleccionarse; los de evidencia C permanecen bloqueados.

La recomendación automática solo carga el tema. La generación continúa siendo individual y requiere una acción consciente para evitar consumos accidentales de API.

El estado resumido del catálogo y el siguiente tema recomendado también están disponibles en `GET /api/indice-temas`, preparado para futuras automatizaciones controladas.

## Ejecución local

Requisitos del sistema:

- Python 3.12;
- FFmpeg y FFprobe;
- las variables indicadas en `.env.example`.

Instale las dependencias y ejecute:

```bash
python -m pip install -r requirements.txt
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

## Producción final

La pantalla `Producción final` se desbloquea cuando la voz y las ocho imágenes están preparadas. Mantiene controles humanos independientes para:

- confirmar las ocho imágenes definitivas;
- revisar y aprobar la sincronización;
- escuchar y aprobar la música;
- revisar el vídeo borrador;
- autorizar la creación del vídeo final.

Las voces nuevas guardan `voz-alineacion.json` a partir de la misma respuesta de ElevenLabs que contiene el audio; no se realiza una segunda generación. Cada imagen incluye una `frase_entrada` literal y única del guion. La Imagen 2 entra obligatoriamente en el segundo 3 y las Imágenes 3 a 8 usan el tiempo real de su frase según ElevenLabs. Una sincronización estimada o sin correspondencia semántica bloquea la aprobación y el montaje.

Antes del render, cada marca temporal se convierte en su fotograma real más próximo a 30 fps. Los cortes se calculan desde posiciones absolutas para que el redondeo no acumule deriva. FFprobe comprueba el número exacto de fotogramas de cada uno de los nueve clips, del vídeo concatenado y del borrador final; cualquier discrepancia bloquea el montaje. El resultado queda guardado en `verificacion_timeline.json`.

El flujo automático no genera ni incrusta subtítulos. Si un proyecto anterior contiene `subtitulos.srt`, se elimina antes del montaje y nunca se incorpora al ZIP. Los fundidos y el zoom se comprueban sobre los fotogramas renderizados; la proporción 9:16 y el margen seguro del sello también se validan. El resultado queda guardado en `verificacion_visual.json`, sin sustituir la aprobación humana del borrador.

La música es obligatoria y debe aprobarse antes del montaje. La aplicación mide los picos de la voz y de la música, ajusta automáticamente la mezcla para conservar un margen mínimo de 14 dB a favor de la voz y aplica un limitador de seguridad. También comprueba que la música siga oyéndose al entrar la Imagen 9 y descienda al menos 12 dB hasta el final. El resultado queda guardado en `verificacion_audio.json`.

Las ocho imágenes del caso terminan con la narración. Después entra durante exactamente 3 segundos el recurso fijo `backend/assets/sello-el-pergamino-perdido.jpeg`. El cierre mantiene todo el texto dentro de una zona segura, aplica un zoom máximo del 2 % y prolonga la música hasta un fundido completo al final de la Imagen 9.

El paquete `proyecto_completo.zip` incluye el vídeo final, imágenes, voz, música, sincronización, `verificacion_timeline.json`, `verificacion_visual.json`, `verificacion_audio.json`, metadatos y textos de publicación. No incluye subtítulos. La publicación y la copia a servicios externos no se ejecutan sin autorización expresa.

## Comprobaciones

```bash
python -m py_compile backend/main.py backend/imagenes.py backend/busqueda_imagenes.py backend/voz.py backend/indice_temas.py backend/produccion.py
python -m unittest discover -s tests -v
```

GitHub Actions ejecuta estas comprobaciones automáticamente en cada PR y en cada cambio de `main`.
