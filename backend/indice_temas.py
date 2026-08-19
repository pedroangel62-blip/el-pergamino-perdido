from copy import deepcopy
from functools import lru_cache
import json
import os
import re
import unicodedata


RUTA_INDICE_TEMAS = os.path.join(
    os.path.dirname(__file__),
    "data",
    "indice_temas.json"
)
ESTADOS_GENERABLES = {"disponible"}
GRADOS_EL_CASO_GENERABLES = {"A", "B"}


def normalizar_clave(texto: str) -> str:
    texto_normalizado = unicodedata.normalize(
        "NFKD",
        str(texto)
    )

    return re.sub(
        r"[^a-z0-9]+",
        " ",
        texto_normalizado.encode(
            "ascii",
            "ignore"
        ).decode("ascii").lower()
    ).strip()


def validar_indice(indice: dict) -> None:
    if not isinstance(indice, dict):
        raise ValueError(
            "El índice de temas no contiene un objeto JSON válido."
        )

    categorias = indice.get("categorias")

    if not isinstance(categorias, list) or not categorias:
        raise ValueError(
            "El índice de temas debe contener categorías."
        )

    ids_categorias = set()
    ids_temas = set()
    titulos = set()

    for categoria in categorias:
        if not isinstance(categoria, dict):
            raise ValueError(
                "Todas las categorías del índice deben ser objetos."
            )

        categoria_id = str(categoria.get("id", "")).strip()
        nombre = str(categoria.get("nombre", "")).strip()
        temas = categoria.get("temas")

        if not categoria_id or categoria_id in ids_categorias:
            raise ValueError(
                "Las categorías deben tener identificadores únicos."
            )

        if not nombre or not isinstance(temas, list):
            raise ValueError(
                f"La categoría {categoria_id} no es válida."
            )

        ids_categorias.add(categoria_id)

        for tema in temas:
            if not isinstance(tema, dict):
                raise ValueError(
                    f"La categoría {categoria_id} contiene un tema inválido."
                )

            tema_id = str(tema.get("id", "")).strip()
            titulo = str(tema.get("titulo", "")).strip()
            estado = str(tema.get("estado", "")).strip()
            titulo_normalizado = normalizar_clave(titulo)

            if not tema_id or tema_id in ids_temas:
                raise ValueError(
                    "Todos los temas deben tener identificadores únicos."
                )

            if not titulo or titulo_normalizado in titulos:
                raise ValueError(
                    "El índice contiene títulos vacíos o duplicados."
                )

            if estado not in {"disponible", "bloqueado"}:
                raise ValueError(
                    f"El tema {tema_id} tiene un estado no permitido."
                )

            if tema.get("origen") == "archivo-el-caso":
                grado = str(
                    tema.get("verificacion", {}).get("grado", "")
                ).strip()

                if (
                    grado not in GRADOS_EL_CASO_GENERABLES
                    and estado != "bloqueado"
                ):
                    raise ValueError(
                        f"El tema {tema_id} debe permanecer bloqueado."
                    )

            ids_temas.add(tema_id)
            titulos.add(titulo_normalizado)


@lru_cache(maxsize=4)
def cargar_indice(
    ruta: str = RUTA_INDICE_TEMAS
) -> dict:
    try:
        with open(
            ruta,
            "r",
            encoding="utf-8"
        ) as archivo:
            indice = json.load(archivo)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "No se pudo cargar el índice maestro de temas."
        ) from error

    validar_indice(indice)

    return indice


def iterar_temas(indice: dict | None = None):
    indice = indice or cargar_indice()

    for categoria in indice["categorias"]:
        for tema in categoria["temas"]:
            yield categoria, tema


def obtener_tema_por_id(tema_id: str) -> dict:
    tema_id = str(tema_id).strip()

    for categoria, tema in iterar_temas():
        if tema.get("id") != tema_id:
            continue

        ficha = deepcopy(tema)
        ficha["categoria"] = {
            "id": categoria["id"],
            "nombre": categoria["nombre"]
        }

        return ficha

    raise ValueError(
        "El tema seleccionado no existe en el índice maestro."
    )


def obtener_tema_por_titulo(titulo: str) -> dict | None:
    titulo_normalizado = normalizar_clave(titulo)

    if not titulo_normalizado:
        return None

    for categoria, tema in iterar_temas():
        titulos_validos = [tema.get("titulo", "")]
        titulos_validos.extend(tema.get("aliases", []))

        if any(
            normalizar_clave(candidato) == titulo_normalizado
            for candidato in titulos_validos
        ):
            ficha = deepcopy(tema)
            ficha["categoria"] = {
                "id": categoria["id"],
                "nombre": categoria["nombre"]
            }

            return ficha

    return None


def obtener_usos_indice(
    directorio_proyectos: str
) -> dict[str, int]:
    usos: dict[str, int] = {}
    ids_validos = {
        tema["id"]
        for _, tema in iterar_temas()
    }

    if not os.path.isdir(directorio_proyectos):
        return usos

    for nombre in sorted(os.listdir(directorio_proyectos)):
        ruta = os.path.join(
            directorio_proyectos,
            nombre,
            "proyecto.json"
        )

        if not os.path.isfile(ruta):
            continue

        try:
            with open(
                ruta,
                "r",
                encoding="utf-8"
            ) as archivo:
                proyecto = json.load(archivo)
        except (OSError, json.JSONDecodeError):
            continue

        referencia = proyecto.get("tema_indice")
        tema_id = ""

        if isinstance(referencia, dict):
            tema_id = str(referencia.get("id", "")).strip()

            if tema_id not in ids_validos:
                tema_id = ""

        if not tema_id:
            ficha = obtener_tema_por_titulo(
                str(proyecto.get("tema", ""))
            )
            tema_id = ficha["id"] if ficha else ""

        if tema_id:
            usos[tema_id] = usos.get(tema_id, 0) + 1

    return usos


def tema_es_generable(tema: dict) -> bool:
    if tema.get("estado") not in ESTADOS_GENERABLES:
        return False

    if tema.get("origen") != "archivo-el-caso":
        return True

    grado = tema.get("verificacion", {}).get("grado")

    return grado in GRADOS_EL_CASO_GENERABLES


def _clave_recomendacion(tema: dict) -> tuple:
    produccion = tema.get("produccion", {})
    puntuacion = produccion.get("puntuacion")
    prioridad = produccion.get("prioridad")

    return (
        puntuacion is None,
        -float(puntuacion) if isinstance(puntuacion, (int, float)) else 0,
        int(prioridad) if isinstance(prioridad, int) else 9999,
        str(tema.get("titulo", ""))
    )


def construir_contexto_indice(
    directorio_proyectos: str
) -> dict:
    indice = cargar_indice()
    usos = obtener_usos_indice(directorio_proyectos)
    categorias_interfaz = []
    candidatos = []
    total_temas = 0
    total_disponibles = 0
    total_bloqueados = 0

    for categoria in sorted(
        indice["categorias"],
        key=lambda elemento: elemento.get("orden", 9999)
    ):
        temas_interfaz = []

        for tema in sorted(
            categoria["temas"],
            key=lambda elemento: (
                elemento.get("produccion", {}).get(
                    "prioridad",
                    9999
                ),
                elemento.get("titulo", "")
            )
        ):
            generable = tema_es_generable(tema)
            veces_usado = usos.get(tema["id"], 0)
            ficha = {
                "id": tema["id"],
                "titulo": tema["titulo"],
                "categoria_id": categoria["id"],
                "categoria_nombre": categoria["nombre"],
                "origen": tema.get("origen", ""),
                "grado": tema.get("verificacion", {}).get(
                    "grado",
                    ""
                ),
                "estado": tema.get("estado", ""),
                "generable": generable,
                "usado": veces_usado > 0,
                "veces_usado": veces_usado,
                "puntuacion": tema.get("produccion", {}).get(
                    "puntuacion"
                ),
                "sensibilidad": tema.get("produccion", {}).get(
                    "sensibilidad",
                    ""
                )
            }
            temas_interfaz.append(ficha)
            total_temas += 1

            if generable:
                total_disponibles += 1

                if not ficha["usado"]:
                    candidatos.append(tema)
            else:
                total_bloqueados += 1

        categorias_interfaz.append({
            "id": categoria["id"],
            "nombre": categoria["nombre"],
            "descripcion": categoria.get("descripcion", ""),
            "temas": temas_interfaz
        })

    recomendado = None

    if candidatos:
        tema_recomendado = min(
            candidatos,
            key=_clave_recomendacion
        )
        recomendado = obtener_tema_por_id(
            tema_recomendado["id"]
        )

    return {
        "indice_categorias": categorias_interfaz,
        "indice_recomendado": recomendado,
        "indice_totales": {
            "temas": total_temas,
            "disponibles": total_disponibles,
            "usados": len(usos),
            "pendientes": sum(
                1
                for categoria in categorias_interfaz
                for tema in categoria["temas"]
                if tema["generable"] and not tema["usado"]
            ),
            "bloqueados": total_bloqueados
        }
    }


def validar_seleccion(
    ficha: dict,
    directorio_proyectos: str
) -> None:
    if not tema_es_generable(ficha):
        raise ValueError(
            "Este tema está bloqueado hasta completar su verificación."
        )

    usos = obtener_usos_indice(directorio_proyectos)

    if usos.get(ficha["id"], 0) > 0:
        raise ValueError(
            "Este tema ya se utilizó en un Pergamino. "
            "Seleccione otro para evitar repeticiones."
        )


def crear_referencia_tema(ficha: dict) -> dict:
    return {
        "id": ficha["id"],
        "titulo": ficha["titulo"],
        "categoria_id": ficha["categoria"]["id"],
        "categoria_nombre": ficha["categoria"]["nombre"],
        "origen": ficha.get("origen", ""),
        "grado_verificacion": ficha.get(
            "verificacion",
            {}
        ).get("grado", ""),
        "fuentes": ficha.get("fuentes", [])
    }


def crear_dossier_generacion(ficha: dict | None) -> str:
    if not ficha:
        return ""

    if ficha.get("origen") != "archivo-el-caso":
        return (
            "\nÍNDICE MAESTRO\n"
            f"Categoría: {ficha['categoria']['nombre']}\n"
            f"Tema: {ficha['titulo']}\n"
            "Estado factual: pendiente de investigación específica. "
            "No invente fechas, citas, descubrimientos ni certezas.\n"
        )

    dossier = {
        "tema": ficha.get("titulo"),
        "categoria": ficha["categoria"]["nombre"],
        "fecha_del_hecho": ficha.get("fecha_hecho"),
        "ubicacion": ficha.get("ubicacion"),
        "estado_del_caso": ficha.get("estado_caso"),
        "resumen_verificado": ficha.get("resumen"),
        "gancho_propuesto": ficha.get("gancho"),
        "evidencia_en_el_caso": ficha.get("evidencia_el_caso"),
        "sensibilidad": ficha.get("produccion", {}).get(
            "sensibilidad"
        ),
        "imagenes_reales_probables": ficha.get("imagenes_reales", []),
        "fuentes": ficha.get("fuentes", [])
    }

    return (
        "\nDOSSIER DOCUMENTAL AUTORIZADO\n"
        f"{json.dumps(dossier, ensure_ascii=False, indent=2)}\n"
        "Use este dossier como límite factual. Distinga hechos, "
        "sentencias, hipótesis y aspectos no resueltos. No añada "
        "detalles morbosos ni atribuya culpabilidad sin respaldo.\n"
    )
