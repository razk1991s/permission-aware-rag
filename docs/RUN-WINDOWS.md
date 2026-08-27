# Running on Windows

The project runs on Windows without WSL or a virtual machine. The code is platform-neutral and uses `pathlib`. Windows does not include `make`, and `psql` may not be installed locally; `make.ps1` replaces `make`, while `psql` runs inside the database container.

## 1. Install prerequisites

| Tool | Purpose | Notes |
|---|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | PostgreSQL + pgvector | WSL2 is used as the backend; there is no need to work inside WSL. |
| [Python 3.12](https://www.python.org/downloads/) | Backend server | Select **Add python.exe to PATH** during installation. |
| [Node.js 22](https://nodejs.org/) | Angular frontend | Use the LTS release. |
| [Ollama](https://ollama.com/download/windows) | Local models | Native installation allows GPU access. |
| [Git](https://git-scm.com/download/win) | Source control | Required for repository work. |

Do not run `docker compose up -d` without a service name. That also starts the Ollama container and conflicts with native Ollama on port 11434. `./make.ps1 up` starts only the database.

## 2. Allow PowerShell scripts

Windows blocks `.ps1` files by default. Run this once in PowerShell:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

If the project came from a downloaded ZIP, unblock the script from the project directory:

```powershell
Unblock-File .\make.ps1
```

## 3. First run

```powershell
Copy-Item .env.example .env
.\make.ps1 install
.\make.ps1 ui-install
.\make.ps1 up
.\make.ps1 models

# Place the corpus package in data\corpus
# Expected path: data\corpus\manifest.json
.\make.ps1 demo
.\make.ps1 ui-build
.\make.ps1 dev            # http://localhost:8000
```

For real embeddings using bge-m3 (about 2.3 GB), install:

```powershell
.\.venv\Scripts\python -m pip install "sentence-transformers>=3.3.0"
```

Use `ingest-fast` when embeddings are not required.

## 4. Development and production modes

Production uses one service and one port:

```powershell
.\make.ps1 ui-build
.\make.ps1 dev            # http://localhost:8000
```

Development uses two terminals with HMR:

```powershell
# Terminal 1
.\make.ps1 dev

# Terminal 2
.\make.ps1 ui-dev         # http://localhost:4200
```

On port 4200, `ng serve` proxies every `/api` request to port 8000. CORS is enabled only in this mode.

## 5. Command translation

| Linux | Windows |
|---|---|
| `make up` | `.\make.ps1 up` |
| `make install` | `.\make.ps1 install` |
| `make demo` | `.\make.ps1 demo` |
| `make dev` | `.\make.ps1 dev` |
| `make test` | `.\make.ps1 test` |
| `make eval` | `.\make.ps1 eval` |
| `make ui-build` | `.\make.ps1 ui-build` |
| `psql "postgresql://..."` | `.\make.ps1 psql` |
| - | `.\make.ps1 models` |

Run `.\make.ps1 help` for the complete list.

## 6. Environment variables in PowerShell

```powershell
$env:LLM_PROVIDER = 'stub'
$env:EMBEDDING_PROVIDER = 'stub'
$env:ENVIRONMENT = 'test'
.\make.ps1 test
```

These values remain set until the terminal closes. To clear them:

```powershell
Remove-Item Env:LLM_PROVIDER, Env:EMBEDDING_PROVIDER, Env:ENVIRONMENT
```

Integration tests require `DATABASE_URL` in the process environment:

```powershell
$env:DATABASE_URL = 'postgresql+asyncpg://rag:ragpass@localhost:5432/ragdb'
.\make.ps1 test
```

Without it, integration tests are skipped automatically.

## 7. Common issues

- **PowerShell script blocked:** run `Unblock-File .\make.ps1` and verify the execution policy.
- **Python is not found:** install Python with **Add python.exe to PATH**, or disable the Windows App execution alias.
- **Hebrew appears corrupted:** use Windows Terminal rather than legacy `cmd.exe`; the script configures UTF-8 output.
- **`docker compose cp` fails:** start Docker Desktop and wait until it is ready.
- **Port 5432 is busy:** set `POSTGRES_PORT=5433` and update `DATABASE_URL` in `.env`.
- **Angular does not detect changes:** move the project out of a network or OneDrive-synchronized folder.
- **Ollama is slow:** run `ollama ps` to verify GPU usage; use the `stub` provider for development and tests without a GPU.
