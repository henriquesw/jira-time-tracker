<#
    Instalacao do Jira Time Tracker 3 — ambiente, icone e atalho, de uma vez so.

    Normalmente nao se chama este arquivo direto: clique duas vezes em
    start.bat. Se o ambiente ainda nao existir, ele traz voce para ca.

    O que acontece aqui:
      1. confirmacao (nada e instalado sem voce dizer que sim);
      2. ambiente Python isolado em %USERPROFILE%\.jira-time-tracker\venv —
         FORA do OneDrive, porque venv dentro de pasta sincronizada corrompe e
         entope o sync;
      3. dependencias la dentro, sem encostar no Python do sistema;
      4. desenho do .ico e criacao do atalho na area de trabalho, apontando
         para pythonw.exe: o programa abre sem nenhuma janela de terminal e
         fica como icone na area de notificacao do Windows.

    As credenciais ficam no Windows Credential Manager e em
    %USERPROFILE%\.jira-time-tracker\config.json. Reinstalar nao mexe nelas.

    Instalar por cima de uma versao antiga ATUALIZA: as dependencias novas
    entram no mesmo ambiente e o atalho passa a apontar para esta pasta.

    Uso:
        powershell -ExecutionPolicy Bypass -File instalar.ps1
        powershell -ExecutionPolicy Bypass -File instalar.ps1 -Silent
        powershell -ExecutionPolicy Bypass -File instalar.ps1 -AtalhoApenas
        powershell -ExecutionPolicy Bypass -File instalar.ps1 -SeNecessario
#>

param(
    [switch]$Silent,        # nao pergunta nada
    [switch]$AtalhoApenas,  # so redesenha o icone e refaz o atalho
    [switch]$SeNecessario   # sai calado se o ambiente ja casa com esta copia
)

$ErrorActionPreference = "Stop"

$ProjectDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$HomeDir     = Join-Path $env:USERPROFILE ".jira-time-tracker"
$VenvDir     = Join-Path $HomeDir "venv"
$VenvPython  = Join-Path $VenvDir "Scripts\python.exe"
$VenvPythonW = Join-Path $VenvDir "Scripts\pythonw.exe"
$AssetsDir   = Join-Path $ProjectDir "assets"
$IconPath    = Join-Path $AssetsDir "jira-time-tracker.ico"
$StampPath   = Join-Path $HomeDir "instalado.json"
$LinkPath    = Join-Path ([Environment]::GetFolderPath("Desktop")) "Jira Time Tracker 3.lnk"
$AppVersion  = "3.1"

# -------------------------------------------------- ja esta atualizado? --- #
# A marca guarda o que foi instalado: versao, pasta de origem e a impressao
# digital do requirements.txt. Sem ela, uma maquina que ja tinha o tracker
# pulava a instalacao inteira - o venv existia, entao nada era atualizado e as
# dependencias novas nunca chegavam.
function Get-Impressao {
    $req = Join-Path $ProjectDir "requirements.txt"
    if (-not (Test-Path $req)) { return "" }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $req).Hash
}

function Test-Atualizado {
    if (-not (Test-Path $VenvPython)) { return $false }
    if (-not (Test-Path $StampPath))  { return $false }
    if (-not (Test-Path $LinkPath))   { return $false }
    try { $marca = Get-Content -LiteralPath $StampPath -Raw | ConvertFrom-Json }
    catch { return $false }
    if ($marca.versao     -ne $AppVersion)     { return $false }
    if ($marca.requisitos -ne (Get-Impressao)) { return $false }
    # Pasta diferente = copia nova em outro lugar: o atalho precisa passar a
    # apontar para ca.
    if ($marca.projeto    -ne $ProjectDir)     { return $false }
    return $true
}

function Save-Marca {
    $marca = [ordered]@{
        versao     = $AppVersion
        projeto    = $ProjectDir
        requisitos = Get-Impressao
        data       = (Get-Date).ToString("s")
    }
    $marca | ConvertTo-Json | Set-Content -LiteralPath $StampPath -Encoding UTF8
}

# Instancias vivas que saiam DESTE ambiente. A versao 2 roda no Python do
# sistema, com outro caminho, e por isso nunca entra nesta lista.
function Get-TrackersVivos {
    $alvos = @($VenvPython, $VenvPythonW) | ForEach-Object { $_.ToLower() }
    return @(Get-Process -Name python, pythonw -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and ($alvos -contains $_.Path.ToLower()) })
}

function Write-Step($text) { Write-Host "`n>> $text" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "   OK  $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "   !!  $text" -ForegroundColor Yellow }

if ($SeNecessario -and (Test-Atualizado)) { exit 0 }

$Atualizando = (Test-Path $VenvPython) -and -not $AtalhoApenas

Write-Host "==============================================================" -ForegroundColor White
if ($Atualizando) { Write-Host "  Jira Time Tracker 3 - atualizacao" -ForegroundColor White }
else              { Write-Host "  Jira Time Tracker 3 - instalacao"  -ForegroundColor White }
Write-Host "==============================================================" -ForegroundColor White

# ------------------------------------------------------------ confirmar --- #
if (-not $Silent -and -not $AtalhoApenas) {
    if (-not $Atualizando) {
        Write-Host @"

  Vai ser instalado nesta maquina:

    - ambiente Python isolado em
      $VenvDir
    - as dependencias do tracker dentro desse ambiente
    - um atalho "Jira Time Tracker 3" na sua area de trabalho

  Nada e instalado no Python do sistema, e suas credenciais nao
  sao tocadas.
"@ -ForegroundColor Gray
    }

    if ($Atualizando) {
        Write-Host @"

  Ja existe um Jira Time Tracker instalado nesta maquina. Vai ser
  atualizado:

    - as dependencias que faltarem, no mesmo ambiente isolado
    - o atalho "Jira Time Tracker 3", que passa a abrir esta pasta:
      $ProjectDir

"@ -ForegroundColor Gray
    }

    $pergunta = if ($Atualizando) { "`n  Atualizar agora? (S/N)" }
                else              { "`n  Instalar agora? (S/N)" }
    $answer = Read-Host $pergunta
    if ($answer -notmatch '^\s*[SsYy]') {
        Write-Host "`nCancelado. Nada foi alterado.`n" -ForegroundColor Yellow
        exit 2
    }
}

# --------------------------------------------------------------- desenho --- #
function New-RoundedPath([single]$size, [single]$radius) {
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $d = $radius * 2
    $path.AddArc(0, 0, $d, $d, 180, 90)
    $path.AddArc($size - $d, 0, $d, $d, 270, 90)
    $path.AddArc($size - $d, $size - $d, $d, $d, 0, 90)
    $path.AddArc(0, $size - $d, $d, $d, 90, 90)
    $path.CloseFigure()
    return $path
}

function New-IconBitmap([int]$size) {
    $bmp = New-Object System.Drawing.Bitmap($size, $size, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb))
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode   = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([System.Drawing.Color]::Transparent)

    # Fundo: quadrado arredondado com degrade indigo -> violeta.
    $radius = [single]($size * 0.235)
    $shape  = New-RoundedPath ([single]$size) $radius
    $rect   = New-Object System.Drawing.RectangleF(0, 0, [single]$size, [single]$size)
    $brush  = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        $rect,
        [System.Drawing.Color]::FromArgb(255, 99, 102, 241),
        [System.Drawing.Color]::FromArgb(255, 139, 92, 246),
        [single]50.0)
    $g.FillPath($brush, $shape)

    # Brilho sutil no topo, para o icone nao ficar chapado.
    $glossRect = New-Object System.Drawing.RectangleF(0, 0, [single]$size, [single]($size * 0.5))
    $gloss = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        $glossRect,
        [System.Drawing.Color]::FromArgb(46, 255, 255, 255),
        [System.Drawing.Color]::FromArgb(0, 255, 255, 255),
        [single]90.0)
    $glossPath = New-RoundedPath ([single]$size) $radius
    $region = New-Object System.Drawing.Region($glossPath)
    $region.Intersect($glossRect)
    $g.FillRegion($gloss, $region)

    # Relogio branco.
    $stroke = [single]([math]::Max(1.4, $size * 0.075))
    $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, $stroke)
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap   = [System.Drawing.Drawing2D.LineCap]::Round

    $inset = [single]($size * 0.26)
    $g.DrawEllipse($pen, $inset, $inset, [single]($size - 2 * $inset), [single]($size - 2 * $inset))

    $center = [single]($size / 2.0)
    $g.DrawLine($pen, $center, [single]($size * 0.335), $center, $center)                  # ponteiro das horas
    $g.DrawLine($pen, $center, $center, [single]($size * 0.655), [single]($size * 0.605))  # ponteiro dos minutos

    $pen.Dispose(); $brush.Dispose(); $gloss.Dispose()
    $shape.Dispose(); $glossPath.Dispose(); $region.Dispose(); $g.Dispose()
    return $bmp
}

function Save-Icon([string]$path, [int[]]$sizes) {
    # ICO com PNG embutido (suportado desde o Vista): cada tamanho entra como um
    # PNG proprio, o que evita o serrilhado de reduzir um bitmap grande.
    $pngs = @()
    foreach ($size in $sizes) {
        $bmp = New-IconBitmap $size
        $stream = New-Object System.IO.MemoryStream
        $bmp.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        $pngs += ,@{ Size = $size; Bytes = $stream.ToArray() }
        $stream.Dispose(); $bmp.Dispose()
    }

    $file = [System.IO.File]::Create($path)
    $writer = New-Object System.IO.BinaryWriter($file)
    try {
        $writer.Write([uint16]0)                 # reservado
        $writer.Write([uint16]1)                 # 1 = icone
        $writer.Write([uint16]$pngs.Count)

        $offset = 6 + (16 * $pngs.Count)
        foreach ($png in $pngs) {
            $dim = if ($png.Size -ge 256) { 0 } else { $png.Size }
            $writer.Write([byte]$dim)            # largura
            $writer.Write([byte]$dim)            # altura
            $writer.Write([byte]0)               # cores na paleta
            $writer.Write([byte]0)               # reservado
            $writer.Write([uint16]1)             # planos
            $writer.Write([uint16]32)            # bits por pixel
            $writer.Write([uint32]$png.Bytes.Length)
            $writer.Write([uint32]$offset)
            $offset += $png.Bytes.Length
        }
        foreach ($png in $pngs) { $writer.Write($png.Bytes) }
    } finally {
        $writer.Dispose(); $file.Dispose()
    }
}

function New-DesktopShortcut {
    Write-Step "Desenhando o icone"
    Add-Type -AssemblyName System.Drawing
    if (-not (Test-Path $AssetsDir)) { New-Item -ItemType Directory -Path $AssetsDir -Force | Out-Null }
    Save-Icon $IconPath @(16, 24, 32, 48, 64, 128, 256)
    Write-Ok $IconPath

    Write-Step "Criando o atalho na area de trabalho"
    # O alvo e o pythonw.exe do ambiente isolado, e nao o start.bat: pythonw
    # roda sem console, entao abrir o tracker nao pisca nenhuma janela preta.
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($LinkPath)
    $shortcut.TargetPath       = $VenvPythonW
    $shortcut.Arguments        = '"' + (Join-Path $ProjectDir "app.py") + '"'
    $shortcut.WorkingDirectory = $ProjectDir
    $shortcut.IconLocation     = "$IconPath,0"
    $shortcut.Description      = "Apontamento de horas no Jira/Tempo"
    $shortcut.Save()
    Write-Ok $LinkPath
}

# -------------------------------------------------- so o icone e o atalho --- #
if ($AtalhoApenas) {
    New-DesktopShortcut
    if (Test-Path $VenvPython) { Save-Marca }
    Write-Host "`nPronto.`n" -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------- Python --- #
Write-Step "Procurando Python 3.10 ou superior"
$python = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $version = & $candidate -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version) {
            $parts = $version.Split(".")
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                $python = $candidate
                Write-Ok "$candidate (versao $version)"
                break
            }
        }
    } catch { }
}
if (-not $python) {
    Write-Host "`nPython 3.10+ nao encontrado. Instale de https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "Marque 'Add Python to PATH' durante a instalacao." -ForegroundColor Red
    exit 1
}

# ------------------------------------------------------------------ venv --- #
Write-Step "Preparando o ambiente isolado em $VenvDir"
if (-not (Test-Path $HomeDir)) { New-Item -ItemType Directory -Path $HomeDir -Force | Out-Null }
if (Test-Path $VenvPython) {
    Write-Ok "ambiente ja existe, reaproveitando"
} else {
    & $python -m venv $VenvDir
    if (-not (Test-Path $VenvPython)) { Write-Host "Falha ao criar o venv." -ForegroundColor Red; exit 1 }
    Write-Ok "ambiente criado"
}

Write-Step "Restringindo o acesso da pasta ao seu usuario"
& icacls $HomeDir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null
Write-Ok "somente $($env:USERNAME) le esta pasta"

Write-Step "Instalando dependencias (pode levar um minuto)"
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet -r (Join-Path $ProjectDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { Write-Host "Falha ao instalar dependencias." -ForegroundColor Red; exit 1 }
Write-Ok "dependencias instaladas no ambiente isolado"

if (-not (Test-Path $VenvPythonW)) {
    Write-Warn "pythonw.exe nao encontrado no ambiente; o atalho vai abrir com console"
}

# ---------------------------------------------- versao antiga ainda viva --- #
# Enquanto a instancia antiga estiver no ar, o atalho novo so reabre a tela
# dela: o programa evita subir duas copias. Entao ela precisa sair de cena.
$vivos = Get-TrackersVivos
if ($vivos.Count) {
    Write-Step "Encerrando a versao que esta aberta"
    $parar = $true
    if (-not $Silent) {
        $resposta = Read-Host "   O tracker esta aberto. Encerrar para concluir? (S/N)"
        $parar = $resposta -match '^\s*[SsYy]'
    }
    if ($parar) {
        $vivos | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 400
        Write-Ok "encerrada - abra pelo atalho de novo"
    } else {
        Write-Warn "encerre pelo icone da bandeja antes de abrir a versao nova"
    }
}

# -------------------------------------------------------- icone e atalho --- #
New-DesktopShortcut
Save-Marca

# ------------------------------------------------------------- proximos --- #
Write-Host "`n==============================================================" -ForegroundColor White
Write-Host "  Pronto" -ForegroundColor White
Write-Host "==============================================================" -ForegroundColor White
Write-Host @"

  Abra pelo atalho "Jira Time Tracker 3" na area de trabalho.
  Nenhuma janela de terminal aparece: o navegador abre sozinho e o
  programa fica como icone em "Mostrar icones ocultos", perto do
  relogio. Para encerrar, clique nele com o botao direito e escolha
  "Encerrar".

  Se for a primeira instalacao nesta maquina, gere seus tokens:
     Jira   https://id.atlassian.com/manage-profile/security/api-tokens
            Create API token (SEM escopos).
     Tempo  Jira > Apps > Tempo > Settings > API Integration > New Token
            Escopo Worklogs: View + Manage. So quem usa Tempo precisa.
  E cole-os na tela de configuracao (engrenagem no canto superior direito).

"@ -ForegroundColor Gray

Write-Host "Instalacao concluida.`n" -ForegroundColor Green
