<#
.SYNOPSIS
    מקבילה ל-Makefile עבור ווינדוס.

.DESCRIPTION
    שימוש:  .\make.ps1 <יעד>
    לרשימת היעדים:  .\make.ps1 help

    שתי החלטות שכדאי להכיר:

    1. psql רץ *בתוך* הקונטיינר, ולכן אין צורך להתקין כלים של פוסטגרס
       על ווינדוס. הקובץ מועתק פנימה ב-`docker compose cp` ולא נשלח
       דרך צינור — צינור של PowerShell ממיר קידוד, וקבצי ה-seed כאן
       מלאים בעברית.

    2. Ollama רץ נייטיב על ווינדוס ולא בקונטיינר, כי כך הוא מקבל את
       ה-GPU. לכן `.\make.ps1 up` מרים רק את מסד הנתונים.

    הסקריפט תואם Windows PowerShell 5.1 (מה שמותקן כברירת מחדל)
    ו-PowerShell 7 כאחד. הקובץ נשמר עם BOM כדי שהעברית תיקרא נכון
    ב-5.1.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string] $Target = 'help'
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $Root

$Venv    = Join-Path $Root '.venv'
$Py      = Join-Path $Venv 'Scripts\python.exe'
$Uvicorn = Join-Path $Venv 'Scripts\uvicorn.exe'

# ניתן לדרוס דרך משתני סביבה, בדיוק כמו ב-Makefile
$Corpus = if ($env:CORPUS)         { $env:CORPUS }         else { 'data/corpus' }
$PgUser = if ($env:POSTGRES_USER)  { $env:POSTGRES_USER }  else { 'rag' }
$PgDb   = if ($env:POSTGRES_DB)    { $env:POSTGRES_DB }    else { 'ragdb' }


# ------------------------------------------------------------------ עזרים

function Write-Step  { param([string] $Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Note  { param([string] $Message) Write-Host "    $Message" -ForegroundColor DarkGray }
function Write-Alert { param([string] $Message) Write-Host "!!  $Message" -ForegroundColor Yellow }

function Invoke-Native {
    <#  מריץ פקודה חיצונית ונופל אם היא נכשלה.
        בלי הבדיקה הזו שגיאה של docker או pip הייתה נבלעת, והיעד הבא
        היה רץ על מצב שבור. #>
    param(
        [Parameter(Mandatory = $true)] [string]   $File,
        [Parameter(Mandatory = $true)] [string[]] $Arguments,
        [Parameter(Mandatory = $true)] [string]   $What
    )
    & $File @Arguments
    # "נכשל: <מה>" ולא "<מה> נכשל" — כדי שהמשפט יתפרק נכון בעברית
    # בלי קשר למין של התיאור שהועבר.
    if ($LASTEXITCODE -ne 0) { throw "Failed: $What (exit code $LASTEXITCODE)" }
}

function Assert-Venv {
    if (-not (Test-Path $Py)) {
        throw "Python virtual environment not found. Run first: .\make.ps1 install"
    }
}

function Assert-Command {
    param([string] $Name, [string] $Hint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found on PATH. $Hint"
    }
}

function Assert-DockerReady {
    Assert-Command 'docker' 'Install Docker Desktop for Windows.'
    $dockerInfo = docker info 2>&1
    $dockerExitCode = $LASTEXITCODE
    if ($dockerExitCode -ne 0) {
        throw 'Docker Desktop is not running. Open Docker Desktop and wait until it is ready.'
    }
}

function Invoke-SqlFile {
    <#  מריץ קובץ SQL בתוך קונטיינר מסד הנתונים.

        ON_ERROR_STOP=1 חשוב: בלעדיו psql ממשיך אחרי שגיאה ומסיים
        עם קוד 0, וטעינה חלקית נראית כמו הצלחה. #>
    param([Parameter(Mandatory = $true)] [string] $Path)

    if (-not (Test-Path $Path)) { throw "SQL file not found: $Path" }

    $name   = [System.IO.Path]::GetFileName($Path)
    $remote = "/tmp/$name"

    Write-Step "psql - $name"
    Invoke-Native 'docker' @('compose', 'cp', $Path, "db:$remote") "Copying $name to the database container"
    Invoke-Native 'docker' @(
        'compose', 'exec', '-T', 'db',
        'psql', '-v', 'ON_ERROR_STOP=1', '-U', $PgUser, '-d', $PgDb, '-f', $remote
    ) "Running $name"
}

function Wait-ForDatabase {
    param([int] $TimeoutSeconds = 90)
    Write-Step 'Waiting for the database...'
    for ($i = 0; $i -lt $TimeoutSeconds; $i++) {
        $null = docker compose exec -T db pg_isready -U $PgUser -d $PgDb 2>&1
        if ($LASTEXITCODE -eq 0) { Write-Note 'Database is ready.'; return }
        Start-Sleep -Seconds 1
    }
    throw "Database did not respond within $TimeoutSeconds seconds. Check: docker compose logs db"
}

function Test-OllamaReady {
    if (-not (Get-Command 'ollama' -ErrorAction SilentlyContinue)) {
        Write-Alert 'Ollama is not installed. Download it from https://ollama.com/download/windows'
        Write-Note  'After installation: .\make.ps1 models'
        return
    }
    $tags = (& ollama list) 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Alert 'Ollama is installed but not running. Open the app or run: ollama serve'
        return
    }
    if ($tags -notmatch 'qwen2\.5') {
        Write-Alert 'Ollama models are not downloaded. Run: .\make.ps1 models'
        return
    }
    Write-Note 'Ollama is ready.'
}

function Invoke-Self {
    param([Parameter(Mandatory = $true)] [string] $Name)
    & $PSCommandPath $Name
}


# ------------------------------------------------------------------ יעדים

# כל היעדים עטופים ב-try אחד כדי שכישלון יראה כמו שורת שגיאה אחת
# ולא כמו stack trace של PowerShell. קוד היציאה נשאר 1, כך שהסקריפט
# עדיין שמיש בתוך CI או בשרשור פקודות.
try {

switch ($Target) {

    'help' {
@'
Usage:  .\make.ps1 <target>

--- Infrastructure ---
    up            Start PostgreSQL (Ollama runs natively on Windows)
    down          Stop the infrastructure
    logs          Show container logs
    install       Create .venv and install Python dependencies
    models        Download Ollama models
    migrate       Run database migrations
    seed          Load demo users and operational data
    index         Build the HNSW index (after ingest)
    demo          Full setup: migrate + seed + ingest + index

--- Server ---
    dev           Run the API with reload on port 8000
    dry-run       Parse and chunk only, without a database
    ingest-fast   Load the corpus without embeddings (fast)
    ingest        Load the corpus with embeddings
    test          Run pytest
    mcp           Run the MCP server over stdio

--- Angular UI ---
    ui-install    Run npm ci in the ui directory
    ui-dev        Run ng serve on port 4200 with a proxy to 8000
    ui-build      Build ui\dist; dev then serves everything on one port
    ui-test       Run Angular unit tests
    ui-lint       Run the TypeScript check (tsc --noEmit)

--- Evaluation ---
    eval          Run the evaluation suite for all configurations
    eval-gate     Run evaluation and regression gates
    injection     Run the prompt-injection suite
    psql          Open an interactive psql shell
    clean         Delete .venv, dist, and cache directories
'@ | Write-Host
    }

    # ---------------------------------------------------------- תשתית

    'up' {
        Assert-DockerReady
        Write-Step 'Starting the database'
        Write-Note 'Only db - Ollama runs natively on Windows for GPU access.'
        Invoke-Native 'docker' @('compose', 'up', '-d', 'db') 'Starting the database'
        Wait-ForDatabase
        Test-OllamaReady
    }

    'down' {
        Invoke-Native 'docker' @('compose', 'down') 'Stopping the infrastructure'
    }

    'logs' {
        docker compose logs -f
    }

    'models' {
        Assert-Command 'ollama' 'הורד מ-https://ollama.com/download/windows'
        Write-Step 'מוריד מודלים (בפעם הראשונה זה כמה דקות)'
        Invoke-Native 'ollama' @('pull', 'qwen2.5:7b-instruct') 'הורדת מודל הייצור'
        Invoke-Native 'ollama' @('pull', 'qwen2.5:3b-instruct') 'הורדת מודל העזר'
    }

    'install' {
        Assert-Command 'python' 'התקן פייתון 3.12 מ-python.org (סמן "Add to PATH").'
        Write-Step 'יוצר סביבה וירטואלית'
        Invoke-Native 'python' @('-m', 'venv', '.venv') 'יצירת venv'
        Write-Step 'מתקין תלויות'
        Invoke-Native $Py @('-m', 'pip', 'install', '-q', '--upgrade', 'pip') 'שדרוג pip'
        Invoke-Native $Py @('-m', 'pip', 'install', '-q', '-r', 'requirements.txt') 'התקנת תלויות'
        Write-Note 'להטמעות אמיתיות:  .\.venv\Scripts\python -m pip install "sentence-transformers>=3.3.0"'
    }

    'migrate' {
        Assert-Venv
        Invoke-Native $Py @('scripts/migrate.py') 'מיגרציות'
    }

    'seed' {
        Invoke-SqlFile (Join-Path $Corpus 'data/seed/seed_auth.sql')
        Invoke-SqlFile (Join-Path $Corpus 'data/seed/seed_operational.sql')
    }

    'index' {
        Invoke-SqlFile 'scripts/build_vector_index.sql'
    }

    'demo' {
        Invoke-Self 'migrate'
        Invoke-Self 'seed'
        Invoke-Self 'ingest'
        Invoke-Self 'index'
        Write-Host ''
        Write-Step 'מוכן.'
        Write-Note 'פרודקשן:  .\make.ps1 ui-build  ואז  .\make.ps1 dev  →  http://localhost:8000'
        Write-Note 'פיתוח:    .\make.ps1 dev  בטרמינל אחד,  .\make.ps1 ui-dev  בשני  →  http://localhost:4200'
    }

    # ---------------------------------------------------------- שרת

    'dev' {
        Assert-Venv
        Invoke-Native $Uvicorn @('app.main:app', '--reload', '--port', '8000') 'הרצת השרת'
    }

    'dry-run' {
        Assert-Venv
        Invoke-Native $Py @('scripts/ingest_corpus.py', '--corpus', $Corpus, '--dry-run') 'הרצה יבשה'
    }

    'ingest-fast' {
        Assert-Venv
        Invoke-Native $Py @('scripts/ingest_corpus.py', '--corpus', $Corpus, '--skip-embeddings') 'טעינת קורפוס'
    }

    'ingest' {
        Assert-Venv
        Invoke-Native $Py @('scripts/ingest_corpus.py', '--corpus', $Corpus) 'טעינת קורפוס'
    }

    'test' {
        Assert-Venv
        Invoke-Native $Py @('-m', 'pytest', '-q', '-p', 'no:warnings') 'בדיקות'
    }

    'mcp' {
        Assert-Venv
        Invoke-Native $Py @('-m', 'mcp_server.server') 'שרת MCP'
    }

    # ---------------------------------------------------------- ממשק

    'ui-install' {
        Assert-Command 'npm' 'התקן Node.js 22 מ-nodejs.org.'
        Push-Location (Join-Path $Root 'ui')
        try {
            if (Test-Path 'package-lock.json') {
                Invoke-Native 'npm' @('ci', '--no-audit', '--no-fund') 'npm ci'
            } else {
                Invoke-Native 'npm' @('install', '--no-audit', '--no-fund') 'npm install'
            }
        } finally { Pop-Location }
    }

    'ui-dev' {
        Push-Location (Join-Path $Root 'ui')
        try { Invoke-Native 'npm' @('start') 'ng serve' } finally { Pop-Location }
    }

    'ui-build' {
        Push-Location (Join-Path $Root 'ui')
        try { Invoke-Native 'npm' @('run', 'build') 'בניית הממשק' } finally { Pop-Location }
        Write-Note 'נבנה ל-ui\dist\browser — ".\make.ps1 dev" יגיש אותו מ-http://localhost:8000'
    }

    'ui-lint' {
        Push-Location (Join-Path $Root 'ui')
        try {
            Invoke-Native 'npx' @('tsc', '--noEmit', '-p', 'tsconfig.app.json') 'בדיקת טיפוסים'
        } finally { Pop-Location }
    }

    'ui-test' {
        Push-Location (Join-Path $Root 'ui')
        try {
            Invoke-Native 'npm' @(
                'test', '--', '--watch=false', '--browsers=ChromeHeadlessNoSandbox'
            ) 'בדיקות Angular'
        } finally { Pop-Location }
    }

    # ---------------------------------------------------------- מדידה

    'eval' {
        Assert-Venv
        Invoke-Native $Py @('scripts/run_eval.py') 'חבילת ההערכה'
    }

    'eval-gate' {
        Assert-Venv
        Invoke-Native $Py @('scripts/run_eval.py', '--config', 'v5-full', '--gate') 'שערי ההערכה'
    }

    'injection' {
        Assert-Venv
        Invoke-Native $Py @('scripts/run_injection.py') 'חבילת ההזרקות'
    }

    'psql' {
        docker compose exec db psql -U $PgUser -d $PgDb
    }

    'clean' {
        Write-Step 'מנקה'
        foreach ($path in @('.venv', '.pytest_cache', 'ui\dist', 'ui\.angular', 'ui\node_modules')) {
            if (Test-Path $path) {
                Remove-Item -Recurse -Force $path
                Write-Note "נמחק: $path"
            }
        }
        Get-ChildItem -Path $Root -Include '__pycache__' -Recurse -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-Item -Recurse -Force $_.FullName }
    }

    default {
        Write-Alert "יעד לא מוכר: $Target"
        Invoke-Self 'help'
        exit 1
    }
}

}
catch {
    Write-Host ''
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
