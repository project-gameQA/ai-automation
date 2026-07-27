<#
.SYNOPSIS
    게임 창과 대시보드 창을 화면 좌우로 나란히 놓는다.

.DESCRIPTION
    이 프로젝트의 시연은 게임 창과 대시보드 창을 함께 화면 녹화하는 형태다. 두 창을 매번 손으로
    끌어다 맞추면 위치가 조금씩 달라져 녹화본마다 구도가 흔들린다. 이 스크립트가 작업 영역
    (작업 표시줄을 뺀 화면)을 재서 정확히 반씩 나눈다.

    게임 창은 **옮기기만 하고 크기는 바꾸지 않는다.** Quake3 계열은 창 크기가 바뀌어도 렌더링
    해상도를 따라 바꾸지 않기 때문이다. GL 컨텍스트가 시작할 때 정해진 크기로 고정되므로,
    창만 늘리면 화면이 늘어나거나 잘린다. 그래서 크기는 게임을 띄울 때 cvar 로 정해야 하고
    (`r_fullscreen 0`, `r_mode -1`, `r_customwidth`, `r_customheight`), 이 스크립트의 -Report 가
    그 값을 계산해 준다. run_qa.bat 이 그 순서로 부른다.

    브라우저 창은 크기를 바꿔도 되므로 나머지 절반에 맞춰 늘린다.

.PARAMETER Report
    창을 옮기지 않고, 게임을 띄울 때 쓸 렌더링 크기(가로 세로)만 출력하고 끝낸다.
    run_qa.bat 이 이 값을 받아 게임 명령줄에 넘긴다.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\arrange_windows.ps1 -Report
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\arrange_windows.ps1
#>

param(
    # 게임 프로세스 이름의 일부다. 실행 파일 이름이 다르면 여기를 바꾼다.
    [string]$GameProcess = "openarena",

    # 대시보드 창을 찾을 때 쓸 제목 조각이다. dashboard/frontend/index.html 의 <title> 과 맞춘다.
    [string]$BrowserTitle = "OpenArena QA Monitor",

    # 게임을 어느 쪽에 놓을지 정한다.
    [ValidateSet("left", "right")][string]$GameSide = "left",

    # 게임이 차지할 가로 비율이다. 0.5 면 정확히 반이다.
    [double]$Split = 0.5,

    # 두 창이 뜰 때까지 기다릴 최대 시간(초)이다. 게임 로딩이 느리면 늘린다.
    [int]$TimeoutSeconds = 40,

    # 창 테두리와 제목 표시줄이 차지하는 여유다. 렌더링 크기를 계산할 때 뺀다.
    [int]$BorderWidth = 16,
    [int]$BorderHeight = 46,

    [switch]$Report
)

# ── Win32 함수 선언 ─────────────────────────────────────────────────────────
# .NET 에는 창을 옮기는 기능이 없어 운영체제 함수를 직접 부른다.
$signature = @"
using System;
using System.Text;
using System.Runtime.InteropServices;

public static class QaWin {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }

    // 이 프로세스가 화면 배율을 스스로 처리한다고 알린다.
    // 이것을 부르지 않으면 배율이 100%가 아닌 화면에서 좌표가 배율만큼 어긋난다.
    // 운영체제가 좌표를 대신 환산해 주는데, 창을 옮길 때 쓰는 좌표는 환산되지 않기 때문이다.
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();

    // 최소화나 최대화된 창을 보통 상태로 되돌린다. 최대화된 창은 옮겨도 자리가 잡히지 않는다.
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")] public static extern bool MoveWindow(
        IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);

    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, ref RECT lpRect);

    // 작업 영역은 화면 전체가 아니라 작업 표시줄을 뺀 영역이다. 화면 크기로 계산하면
    // 창 아래쪽이 작업 표시줄에 가린다.
    [DllImport("user32.dll", SetLastError = true)] public static extern bool SystemParametersInfo(
        uint uiAction, uint uiParam, ref RECT pvParam, uint fWinIni);

    // ── 제목으로 창 찾기 ────────────────────────────────────────────────
    // .NET 의 Process.MainWindowTitle 을 쓰지 않는 이유가 있다. 그것은 프로세스마다 창을
    // 하나만 알려 준다. 브라우저는 창을 여러 개 띄울 수 있고 우리 페이지가 그중 '주' 창이
    // 아닐 수 있으므로, 그 방식으로는 열려 있는데도 못 찾는 경우가 생긴다.
    // 여기서는 화면에 보이는 최상위 창을 전부 훑어 제목이 맞는 것을 고른다.
    public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

    public static IntPtr FindByTitle(string part) {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr h, IntPtr l) {
            if (!IsWindowVisible(h)) return true;          // 숨은 창은 건너뛴다
            StringBuilder sb = new StringBuilder(512);
            if (GetWindowText(h, sb, sb.Capacity) == 0) return true;   // 제목 없는 창도 건너뛴다
            if (sb.ToString().IndexOf(part, StringComparison.OrdinalIgnoreCase) >= 0) {
                found = h;
                return false;                             // 찾았으므로 훑기를 멈춘다
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
"@

Add-Type -TypeDefinition $signature -ErrorAction Stop

$SPI_GETWORKAREA = 0x0030
$SW_RESTORE = 9

[void][QaWin]::SetProcessDPIAware()

# ── 작업 영역 재기 ──────────────────────────────────────────────────────────
$area = New-Object 'QaWin+RECT'
if (-not [QaWin]::SystemParametersInfo($SPI_GETWORKAREA, 0, [ref]$area, 0)) {
    Write-Host "  작업 영역을 재지 못했다. 창 배치를 건너뛴다."
    exit 1
}
$workX = $area.Left
$workY = $area.Top
$workW = $area.Right - $area.Left
$workH = $area.Bottom - $area.Top

# 게임 쪽 너비와 브라우저 쪽 너비를 나눈다. 나머지를 브라우저에 주어 두 창을 합치면
# 작업 영역과 정확히 같아지게 한다(반올림으로 한 픽셀이 비는 것을 막는다).
$gameWidth = [int]($workW * $Split)
$browserWidth = $workW - $gameWidth

if ($GameSide -eq "left") {
    $gameX = $workX
    $browserX = $workX + $gameWidth
} else {
    $browserX = $workX
    $gameX = $workX + $browserWidth
}

# ── -Report: 게임을 띄울 때 쓸 렌더링 크기만 알려 준다 ──────────────────────
if ($Report) {
    # 창 테두리와 제목 표시줄을 뺀 값이 실제로 그려질 영역이다. 이 값을 r_customwidth /
    # r_customheight 에 넣어야 창 전체가 배정된 자리에 들어맞는다.
    $renderW = [Math]::Max(320, $gameWidth - $BorderWidth)
    $renderH = [Math]::Max(240, $workH - $BorderHeight)

    # 화면 비율도 함께 알려 준다. 표준 해상도가 아닌 크기로 띄우면 비율을 스스로 계산하지
    # 못하는 갈래가 있어, 그런 경우 화면이 옆으로 찌그러진다. 비율을 명시하면 어느 갈래에서도
    # 바르게 나온다(쓰지 않는 갈래에서는 그냥 무시되는 값이다).
    $aspect = [double]$renderW / [double]$renderH
    $aspectText = $aspect.ToString("0.####", [System.Globalization.CultureInfo]::InvariantCulture)

    # 배치 파일이 `set "이름=값"` 으로 그대로 받을 수 있게 한 줄에 하나씩 낸다. 한 줄에 여러
    # 값을 내면 배치 쪽에서 변수 여러 개를 한 줄에 설정해야 하는데, 그 문법은 반복 구간 안에서
    # 동작이 미묘해 값이 비는 경우가 있다.
    Write-Output "GAME_W=$renderW"
    Write-Output "GAME_H=$renderH"
    Write-Output "GAME_ASPECT=$aspectText"
    exit 0
}

# ── 창 찾기 ─────────────────────────────────────────────────────────────────
function Wait-ForWindow {
    param(
        [scriptblock]$Finder,   # 창 핸들을 찾아 돌려주는 함수
        [string]$Label,         # 안내 문구에 쓸 이름
        [int]$Seconds
    )
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $handle = & $Finder
        if ($handle -and $handle -ne [IntPtr]::Zero) {
            return $handle
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "  $Label 창을 찾지 못했다(${Seconds}초 기다림)."
    return [IntPtr]::Zero
}

# 게임은 프로세스 이름으로 찾는다. 창 제목은 맵에 따라 바뀌지만 프로세스 이름은 고정이다.
$findGame = {
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -like "*$GameProcess*" -and $_.MainWindowHandle -ne 0 } |
        Select-Object -First 1 -ExpandProperty MainWindowHandle
}

# 브라우저는 제목으로 찾는다. 어떤 브라우저를 쓰는지 알 수 없어 프로세스 이름으로는 찾을 수
# 없고, 여러 창·여러 탭 중 우리 페이지가 열린 창을 골라야 하기 때문이다.
$findBrowser = { [QaWin]::FindByTitle($BrowserTitle) }

$gameHandle = Wait-ForWindow -Finder $findGame -Label "게임" -Seconds $TimeoutSeconds
# 대시보드는 게임보다 늦게 준비될 수 있다. 개발 서버(vite)를 처음 띄우면 의존성을 정리하느라
# 페이지가 나오기까지 시간이 걸리고, 그전에는 창 제목이 정해지지 않는다.
$browserHandle = Wait-ForWindow -Finder $findBrowser -Label "대시보드" -Seconds 25

# ── 배치 ────────────────────────────────────────────────────────────────────
if ($gameHandle -ne [IntPtr]::Zero) {
    [void][QaWin]::ShowWindow($gameHandle, $SW_RESTORE)

    # 게임 창은 크기를 건드리지 않는다. 지금 크기를 그대로 읽어 자리만 옮긴다.
    # 크기를 바꾸면 창만 커지고 그려지는 화면은 그대로라 늘어나거나 잘린다.
    $rect = New-Object 'QaWin+RECT'
    [void][QaWin]::GetWindowRect($gameHandle, [ref]$rect)
    $w = $rect.Right - $rect.Left
    $h = $rect.Bottom - $rect.Top

    [void][QaWin]::MoveWindow($gameHandle, $gameX, $workY, $w, $h, $true)
    Write-Host "  게임 창을 옮겼다. ($gameX, $workY) 크기 ${w}x${h}"

    if ($w -gt $gameWidth + 4) {
        # 전체 화면이거나 배정된 폭보다 큰 창이다. 크기는 게임을 띄울 때 정해지므로
        # 여기서는 고칠 수 없고, 다음 실행부터 맞도록 안내한다.
        Write-Host "  참고: 창이 배정된 폭(${gameWidth})보다 크다. run_qa.bat 의 창 배치 설정이"
        Write-Host "        켜져 있는지, 게임이 창 모드(r_fullscreen 0)인지 확인한다."
    }
}

if ($browserHandle -ne [IntPtr]::Zero) {
    # 브라우저는 크기를 바꿔도 내용이 그에 맞게 다시 그려지므로 남은 폭에 꽉 채운다.
    [void][QaWin]::ShowWindow($browserHandle, $SW_RESTORE)
    [void][QaWin]::MoveWindow($browserHandle, $browserX, $workY, $browserWidth, $workH, $true)
    Write-Host "  대시보드 창을 옮겼다. ($browserX, $workY) 크기 ${browserWidth}x${workH}"
} else {
    # 실제로 일어나는 원인은 셋이다. 어느 쪽이든 창이 제대로 뜬 뒤 이 스크립트를 다시 실행하면 된다.
    Write-Host "  대시보드 창을 제목 '$BrowserTitle' 으로 찾지 못했다. 아래를 확인한다."
    Write-Host "    - 개발 서버가 아직 준비되지 않아 브라우저에 연결 실패 화면이 떠 있는 경우."
    Write-Host "      새로 고침한 뒤 이 스크립트를 다시 실행한다."
    Write-Host "    - run_qa.bat 의 OPEN_BROWSER 가 0 이라 대시보드를 열지 않은 경우."
    Write-Host "    - index.html 의 <title> 이 이 이름과 다른 경우."
}

exit 0
