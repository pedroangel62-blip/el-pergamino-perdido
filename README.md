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
8. La aplicación sincroniza frases, subtítulos e imágenes con las marcas reales de la voz, con portada fija de 3 segundos.
9. El usuario carga y aprueba la música antes de mezclarla con la voz.
10. FFmpeg genera un borrador vertical 1080×1920 a 30 fps con subtítulos y transiciones.
11. El borrador aprobado se convierte en vídeo final y paquete ZIP descargable.
12. Cada Pergamino se conserva en su propia carpeta con `proyecto.json` como fuente de verdad.

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

Las voces nuevas guardan `voz-alineacion.json` a partir de la misma respuesta de ElevenLabs que contiene el audio; no se realiza una segunda generación. Los cortes posteriores a la portada se ajustan a tiempos reales y priorizan comienzos naturales de frase. Si un proyecto antiguo no contiene esas marcas, la interfaz identifica claramente la sincronización como estimada para que pueda revisarse o regenerarse.

El paquete `proyecto_completo.zip` incluye el vídeo final, imágenes, voz, música, subtítulos, sincronización, metadatos y textos de publicación. La publicación y la copia a servicios externos no se ejecutan sin autorización expresa.

## Comprobaciones

```bash
python -m py_compile backend/main.py backend/imagenes.py backend/busqueda_imagenes.py backend/voz.py backend/indice_temas.py backend/produccion.py
python -m unittest discover -s tests -v
```

GitHub Actions ejecuta estas comprobaciones automáticamente en cada PR y en cada cambio de `main`.
