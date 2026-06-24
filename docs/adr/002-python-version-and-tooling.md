# ADR-002: Python 3.12 fijado con uv como gestor de entornos

**Estado:** Aceptado  
**Fecha:** 2026-06-24  
**Autores:** Equipo DentalBot

---

## Contexto

El servidor WSL del desarrollador tiene Python 3.14 como intérprete de sistema, que:
- No incluye `pip` (la instalación de sistema viene sin él).
- Es una versión mayor que la especificada en CLAUDE.md (3.12), lo que introduce riesgo de incompatibilidades con dependencias del proyecto.

Necesitamos fijar Python 3.12 de forma reproducible, tanto en desarrollo local como en CI/CD y Docker.

## Decisión

### Gestor de entornos: `uv`

`uv` (Astral) reemplaza el trío `pyenv + virtualenv + pip` con una sola herramienta que:
- Descarga e instala intérpretes Python específicos (`uv python install 3.12`).
- Crea entornos virtuales vinculados a esa versión (`uv venv --python 3.12`).
- Instala dependencias en segundos (escrito en Rust, 10–100× más rápido que pip).
- Se instala sin privilegios de root con el script oficial de Astral.

### Versión: Python 3.12

- Es la versión indicada en CLAUDE.md ("Python 3.12 + FastAPI").
- Tiene soporte activo hasta octubre 2028.
- Compatible con todas las dependencias del stack (FastAPI 0.115, SQLAlchemy 2.0, Pydantic v2, asyncpg).

### Archivos que fijan la versión

| Archivo | Propósito |
|---------|-----------|
| `.python-version` | Herramienta de herramientas como `uv`, `pyenv`, `mise` para autodetección |
| `backend/Dockerfile` | Imagen base `python:3.12-slim` |
| `backend/.venv/` | Entorno virtual local (git-ignorado) |

## Consecuencias

- **Positivo:** Versión de Python reproducible en cualquier máquina sin depender del intérprete de sistema.
- **Positivo:** Instalación de dependencias drásticamente más rápida (46 paquetes en ~1s vs. minutos con pip).
- **Positivo:** `uv` genera lockfiles compatibles con pip (`requirements.txt` sigue siendo la fuente de verdad en el MVP).
- **Negativo:** `uv` es una dependencia adicional del entorno de desarrollo. Si un colaborador no lo tiene, debe instalarlo (un solo comando).
- **Mitigación:** El `README.md` documenta los pasos de instalación. En CI/CD, la imagen Docker no necesita `uv` porque usa la imagen base de Python directamente.
