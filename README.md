# El Pergamino Perdido

Aplicación web para preparar Reels documentales de El Pergamino Perdido con control humano de guion, voz, material visual y publicación.

## Flujo actual

1. El índice maestro recomienda el siguiente tema disponible y no utilizado.
2. El usuario confirma el tema antes de consumir créditos.
3. La aplicación genera y guarda guion, plan visual y textos de publicación.
4. ElevenLabs genera la narración desde el guion aprobado.
5. El usuario escucha y aprueba la voz; hasta entonces las imágenes permanecen bloqueadas.
6. Se buscan fotografías reales antes de ofrecer una recreación con IA.
7. Cada Pergamino se conserva en su propia carpeta con `proyecto.json` como fuente de verdad.

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

Configure las variables indicadas en `.env.example` y ejecute:

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

## Comprobaciones

```bash
python -m py_compile backend/main.py backend/imagenes.py backend/busqueda_imagenes.py backend/voz.py backend/indice_temas.py
python -m unittest discover -s tests -v
```
