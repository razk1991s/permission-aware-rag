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
    if ($LASTEXITCODE -ne 0) { throw "נכשל: $What (קוד יציאה $LASTEXITCODE)" }
}

function Assert-Venv {
    if (-not (Test-Path $Py)) {
        throw "סביבת הפייתון לא נמצאה. הרץ קודם:  .\make.ps1 install"
    }
}

function Assert-Command {
    param([string] $Name, [string] $Hint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name לא נמצא ב-PATH. $Hint"
    }
}

function Invoke-SqlFile {
    <#  מריץ קובץ SQL בתוך קונטיינר מסד הנתונים.

        ON_ERROR_STOP=1 חשוב: בלעדיו psql ממשיך אחרי שגיאה ומסיים
        עם קוד 0, וטעינה חלקית נראית כמו הצלחה. #>
    param([Parameter(Mandatory = $true)] [string] $Path)

    if (-not (Test-Path $Path)) { throw "קובץ SQL לא נמצא: $Path" }

    $name   = [System.IO.Path]::GetFileName($Path)
    $remote = "/tmp/$name"

    Write-Step "psql — $name"
    Invoke-Native 'docker' @('compose', 'cp', $Path, "db:$remote") "העתקת $name לקונטיינר"
    Invoke-Native 'docker' @(
        'compose', 'exec', '-T', 'db',
        'psql', '-v', 'ON_ERROR_STOP=1', '-U', $PgUser, '-d', $PgDb, '-f', $remote
    ) "הרצת $name"
}

function Wait-ForDatabase {
    param([int] $TimeoutSeconds = 90)
    Write-Step 'ממתין למסד הנתונים...'
    for ($i = 0; $i -lt $TimeoutSeconds; $i++) {
        $null = docker compose exec -T db pg_isready -U $PgUser -d $PgDb 2>&1
        if ($LASTEXITCODE -eq 0) { Write-Note 'מסד הנתונים מוכן.'; return }
        Start-Sleep -Seconds 1
    }
    throw "מסד הנתונים לא ענה תוך $TimeoutSeconds שניות. בדוק:  docker compose logs db"
}

function Test-OllamaReady {
    if (-not (Get-Command 'ollama' -ErrorAction SilentlyContinue)) {
        Write-Alert 'Ollama לא מותקן. הורד מ-https://ollama.com/download/windows'
        Write-Note  'אחרי ההתקנה:  .\make.ps1 models'
        return
    }
    $tags = (& ollama list) 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Alert 'Ollama מותקן אבל לא רץ. פתח את האפליקציה, או הרץ:  ollama serve'
        return
    }
    if ($tags -notmatch 'qwen2\.5') {
        Write-Alert 'המודלים עוד לא הורדו. הרץ:  .\make.ps1 models'
        return
    }
    Write-Note 'Ollama מוכן.'
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
שימוש:  .\make.ps1 <יעד>

--- תשתית ---
  up            מרים PostgreSQL (Ollama רץ נייטיב, לא בקונטיינר)
  down          עוצר את התשתית
  logs          לוגים של הקונטיינרים
  install       יוצר .venv ומתקין תלויות פייתון
  models        מוריד את מודלי Ollama
  migrate       מריץ מיגרציות
  seed          טוען משתמשי דמו ונתונים תפעוליים
  index         בונה אינדקס HNSW (אחרי ingest)
  demo          טעינה מלאה מאפס: migrate + seed + ingest + index

--- שרת ---
  dev           מריץ את ה-API עם reload על 8000
  dry-run       פרסור וחיתוך בלבד, בלי מסד נתונים
  ingest-fast   טעינת הקורפוס בלי הטמעות (מהיר)
  ingest        טעינת הקורפוס עם הטמעות
  test          pytest
  mcp           שרת MCP על stdio

--- ממשק (Angular) ---
  ui-install    npm ci בתיקיית ui
  ui-dev        ng serve על 4200 עם proxy ל-8000
  ui-build      בונה ל-ui\dist; אחריו 'dev' מגיש הכול מפורט אחד
  ui-test       בדיקות יחידה של Angular
  ui-lint       בדיקת טיפוסים (tsc --noEmit)

--- מדידה ---
  eval          חבילת ההערכה, כל הקונפיגורציות
  eval-gate     הערכה + שערי רגרסיה
  injection     חבילת ההזרקות
  psql          מסוף psql אינטראקטיבי
  clean         מוחק .venv, dist ותיקיות מטמון
'@ | Write-Host
    }

    # ---------------------------------------------------------- תשתית

    'up' {
        Assert-Command 'docker' 'התקן Docker Desktop for Windows.'
        Write-Step 'מרים את מסד הנתונים'
        Write-Note 'רק db — Ollama רץ נייטיב על ווינדוס כדי לקבל GPU.'
        Invoke-Native 'docker' @('compose', 'up', '-d', 'db') 'הרמת מסד הנתונים'
        Wait-ForDatabase
        Test-OllamaReady
    }

    'down' {
        Invoke-Native 'docker' @('compose', 'down') 'עצירת התשתית'
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
    Write-Host "שגיאה: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
