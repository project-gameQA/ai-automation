"""
fault_injector.py
=================
게임 프로세스에 '진짜' 결함을 주입하는 모듈. (보험용)

왜 있나:
    시연용 게임(Maze Trials)은 상용 게임이라 자체 버그가 거의 없다.
    실제 세션 데이터를 확인한 결과:
        · window_responsive : 8,953행 전부 True (응답없음 0건)
        · cpu_percent       : 최대 51.3% (임계치 90% 초과 0건)
        · crash / hang      : 23,570개 이벤트 중 0건
    즉 정상 세션만으로는 "검출된 게임 결함 0건"이 나온다.

    이 모듈은 실제 게임 프로세스를 정지/저하/종료시켜서
    - 관객이 화면에서 눈으로 볼 수 있고
    - 에이전트(monitor.py)가 실제로 탐지해 로그에 남기는
    진짜 결함을 만든다. (QA 업계 정식 기법: Fault Injection)

주의사항:
    1) Windows의 IsHungAppWindow는 약 5초 이상 무응답이어야 True를 반환한다.
       → suspend는 최소 8초 이상 유지해야 확실히 탐지된다.
    2) monitor.py는 hang이 '풀렸다가 다시 걸려야' 새 이벤트를 남긴다.
       → suspend → resume → suspend 사이클로 여러 번 잡아야 한다.
    3) 정지된 프로세스를 되살리지 않고 스크립트가 죽으면 게임이 영원히 얼어붙는다.
       → atexit + finally로 무조건 복구한다.

사용 (단독 실행):
    python fault_injector.py --target "Maze Trials" --scenario demo

사용 (UI에서 import):
    from fault_injector import FaultInjector, find_process_by_window_title
    pid = find_process_by_window_title("Maze Trials")
    with FaultInjector(pid, on_log=print) as fi:
        fi.hang(8)
        fi.stutter(12)
        fi.crash()
"""

import argparse
import atexit
import sys
import time

try:
    import psutil
except ImportError:
    print("psutil이 필요합니다:  pip install psutil")
    raise

try:
    import win32gui
    import win32process
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False


# ────────────────────────────────────────────────────────────
# 안전장치: 프로그램이 어떻게 죽든 정지된 프로세스는 반드시 되살린다
# ────────────────────────────────────────────────────────────

_SUSPENDED_PIDS = set()


def _emergency_resume_all():
    """
    인터프리터 종료 시 자동 호출된다 (atexit 등록).
    예외로 죽든 Ctrl+C로 죽든 정지된 게임을 반드시 풀어준다.
    이게 없으면 스크립트가 죽는 순간 게임이 영구 동결된다.
    """
    for pid in list(_SUSPENDED_PIDS):
        try:
            psutil.Process(pid).resume()
            print(f"[안전복구] pid={pid} 정지 해제됨")
        except Exception:
            pass   # 이미 죽었거나 권한 없으면 할 수 있는 게 없다
        _SUSPENDED_PIDS.discard(pid)


atexit.register(_emergency_resume_all)


# ────────────────────────────────────────────────────────────
# 대상 프로세스 찾기
# ────────────────────────────────────────────────────────────

def find_process_by_window_title(title_substring):
    """
    창 제목에 title_substring이 포함된 창의 pid를 반환. 못 찾으면 None.

    에이전트(GamingAI/gameqa/target.py)가 창을 찾는 방식과 동일해야
    같은 프로세스를 잡는다.
    """
    if not _HAS_WIN32:
        raise RuntimeError(
            "창 제목으로 찾으려면 pywin32가 필요합니다: pip install pywin32\n"
            "또는 --pid로 직접 지정하세요.")

    match = title_substring.lower()
    found = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        if match in title.lower():
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            found.append((pid, title))

    win32gui.EnumWindows(callback, None)

    if not found:
        return None

    if len(found) > 1:
        print(f"[경고] '{title_substring}'와 일치하는 창이 {len(found)}개입니다:")
        for pid, title in found:
            print(f"       pid={pid}  title={title!r}")
        print(f"       → 첫 번째(pid={found[0][0]})를 사용합니다.")

    return found[0][0]

# ────────────────────────────────────────────────────────────
# 데모 에러 삽입(fault_injector.py)
# ────────────────────────────────────────────────────────────

from PyQt6.QtWidgets import QMessageBox

# 예시: UI 클래스 내부의 메서드
def on_btn_demo_clicked(self):
    target_title = "Maze Trials"
    pid = find_process_by_window_title(target_title)
    
    if not pid:
        QMessageBox.warning(self, "경고", f"'{target_title}' 게임이 실행되어 있지 않습니다!")
        return

    # 워커 스레드와 별개로, 게임 프로세스에 직접 결함 주입
    # UI가 멈추지 않도록 주의 (필요시 이 부분도 별도 스레드로 분리 가능)
    try:
        with FaultInjector(pid, on_log=self.log_to_ui) as fi:
            self.log_to_ui("결함 주입 데모 시작...")
            fi.hang(8)     # 8초간 응답없음 유발 (높음 심각도)
            fi.stutter(12) # 성능 저하 유발 (중간 심각도)
            # fi.crash()   # 크래시는 게임이 완전히 꺼지므로 필요할 때만 주석 해제
    except Exception as e:
        self.log_to_ui(f"결함 주입 중 오류 발생: {e}")



# ────────────────────────────────────────────────────────────
# 결함 주입기 본체
# ────────────────────────────────────────────────────────────

class FaultInjector:
    """
    지정한 프로세스에 결함을 주입한다.

    반드시 with문으로 쓸 것. 블록을 어떻게 빠져나가든
    (정상 종료 / 예외 / Ctrl+C) __exit__가 정지 상태를 풀어준다.

        with FaultInjector(pid) as fi:
            fi.hang(8)
        # 여기 도달하면 무조건 resume + 우선순위 복구됨
    """

    def __init__(self, pid, on_log=None):
        """
        Args:
            on_log: 로그 콜백. UI에서 쓸 때
                    lambda msg: worker.log_signal.emit(msg) 를 넘기면
                    주입 과정이 UI 로그창에 실시간으로 뜬다.
        """
        self.pid = pid
        # 프로세스가 없으면 여기서 바로 예외.
        # (시연 중간에 터지는 것보다 일찍 실패하는 게 낫다)
        self.proc = psutil.Process(pid)

        self._log = on_log if on_log is not None else print

        # degrade() 후 복구용. 실패하면 None으로 두고 복구를 건너뛴다.
        try:
            self._original_nice = self.proc.nice()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            self._original_nice = None

        self._is_suspended = False
        self._log(f"🎯 대상 확보: pid={pid} name={self._safe_name()}")

    # ── 내부 헬퍼 ──────────────────────────────────────────

    def _safe_name(self):
        try:
            return self.proc.name()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            return "?"

    def is_alive(self):
        """프로세스가 아직 살아있는지. 좀비도 죽은 것으로 본다."""
        try:
            return (self.proc.is_running()
                    and self.proc.status() != psutil.STATUS_ZOMBIE)
        except psutil.NoSuchProcess:
            return False

    # ── 결함 1: 응답없음(Hang) ─────────────────────────────

    def suspend(self):
        """프로세스 정지. 화면이 그 자리에서 얼어붙는다."""
        if self._is_suspended:
            return
        self.proc.suspend()
        self._is_suspended = True
        _SUSPENDED_PIDS.add(self.pid)   # 비상 복구 목록에 등록
        self._log("🧊 프로세스 정지 — 게임 화면이 멈춥니다")

    def resume(self):
        """정지된 프로세스를 다시 실행."""
        if not self._is_suspended:
            return
        self.proc.resume()
        self._is_suspended = False
        _SUSPENDED_PIDS.discard(self.pid)
        self._log("▶️ 프로세스 재개 — 게임이 다시 움직입니다")

    def hang(self, duration_s=8.0):
        """
        지정 시간만큼 정지시켰다가 자동으로 되살린다.

        ⚠️ 8초 이상을 권장. Windows의 IsHungAppWindow는 약 5초 이상
           무응답이어야 True를 반환하므로 그보다 짧으면 탐지가 안 된다.
        """
        if duration_s < 6:
            self._log(f"⚠️ {duration_s}초는 너무 짧습니다. "
                      f"Windows 응답없음 판정에는 5초 이상 필요합니다.")

        self._log(f"💉 [결함 주입] 응답없음 {duration_s}초")
        self.suspend()
        try:
            # 여기서 예외가 나거나 Ctrl+C가 눌려도 finally가 resume한다
            time.sleep(duration_s)
        finally:
            self.resume()

    # ── 결함 2: 성능 저하 ──────────────────────────────────

    def stutter(self, cycles=12, freeze_ms=150, run_ms=80):
        """
        아주 짧은 정지/재개를 반복해 '끊김(프레임 드랍)'을 만든다.

        hang과 다른 점:
            각 정지가 5초 미만이라 '응답없음'으로 판정되지 않는다.
            대신 화면이 뚝뚝 끊기고 perf.csv의 cpu_percent가 떨어진다.
            → '치명적 멈춤'이 아닌 '성능 저하'로 구분된다.
        """
        total = cycles * (freeze_ms + run_ms) / 1000
        self._log(f"💉 [결함 주입] 성능 저하 — {cycles}회 끊김 (약 {total:.1f}초)")

        try:
            for _ in range(cycles):
                if not self.is_alive():
                    self._log("⚠️ 프로세스가 종료되어 중단합니다.")
                    break
                self.proc.suspend()
                self._is_suspended = True
                _SUSPENDED_PIDS.add(self.pid)
                time.sleep(freeze_ms / 1000)
                self.proc.resume()
                self._is_suspended = False
                _SUSPENDED_PIDS.discard(self.pid)
                time.sleep(run_ms / 1000)
        finally:
            # 루프 도중 예외가 나면 정지 상태로 남을 수 있으니 확실히 푼다
            self.resume()

        self._log("✅ 성능 저하 구간 종료")

    def degrade(self):
        """
        프로세스 우선순위를 최하로 낮춰 CPU를 굶긴다.

        ⚠️ CPU가 한가하면 효과가 거의 없다(경쟁할 상대가 없으므로).
           확실한 시연을 원하면 stutter를 쓸 것.
        """
        try:
            self.proc.nice(psutil.IDLE_PRIORITY_CLASS)
            self._log("💉 [결함 주입] 프로세스 우선순위를 최하로 낮춤")
        except (psutil.AccessDenied, AttributeError) as e:
            # AccessDenied: 관리자 권한 필요 | AttributeError: 비Windows
            self._log(f"⚠️ 우선순위 변경 실패: {e}")

    def restore_priority(self):
        """degrade()로 낮춘 우선순위를 되돌린다."""
        if self._original_nice is None:
            return
        try:
            self.proc.nice(self._original_nice)
            self._log("✅ 우선순위 복구됨")
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

    # ── 결함 3: 크래시 ─────────────────────────────────────

    def crash(self):
        """
        프로세스를 강제 종료. 게임 창이 사라진다.
        monitor.py가 이를 감지해 crash 이벤트를 종료코드와 함께 기록한다.

        ⚠️ 되돌릴 수 없다. 시나리오의 마지막에 배치할 것.
        """
        self._log("💉 [결함 주입] 강제 종료 — 크래시 유발")

        # 정지 상태에서 kill하면 종료 처리가 꼬일 수 있으니 먼저 푼다
        if self._is_suspended:
            self.resume()

        try:
            self.proc.kill()
            self.proc.wait(timeout=3)   # kill은 비동기다
            self._log("💥 프로세스 종료 완료 — 게임 창이 사라집니다")
        except psutil.NoSuchProcess:
            self._log("⚠️ 프로세스가 이미 종료된 상태였습니다.")
        except psutil.TimeoutExpired:
            self._log("⚠️ 종료 대기 시간 초과 (아직 살아있을 수 있음)")

    # ── 컨텍스트 매니저 ────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False   # 예외를 삼키지 않고 그대로 위로 전달

    def cleanup(self):
        """정지 해제 + 우선순위 복구. 여러 번 불러도 안전하다."""
        try:
            self.resume()
        except Exception:
            pass
        try:
            self.restore_priority()
        except Exception:
            pass


# ────────────────────────────────────────────────────────────
# 시연 시나리오
# ────────────────────────────────────────────────────────────

def run_demo_scenario(injector, on_log=None, should_stop=None):
    """
    발표용 결함 주입 시나리오. 총 약 100초.

        0:00  대기 — 정상 로그가 흐르는 구간
        0:20  응답없음 8초   → 🔴 높음
        0:40  성능저하       → 🟡 중간
        1:00  응답없음 12초  → 🔴 높음 (더 심각)
        1:30  크래시         → ⛔ 치명 (세션 종료)

    뒤로 갈수록 심각해져서 극적이고, 크래시로 자연스럽게 끝난다.

    Args:
        should_stop: True를 반환하면 시나리오를 중단한다 (UI 중지 버튼용)
    """
    log = on_log if on_log is not None else injector._log

    def wait(seconds, reason):
        """대기 구간. 이 동안 정상 로그가 흐른다."""
        log(f"⏳ {reason} ({seconds}초)")
        for _ in range(int(seconds)):
            if should_stop and should_stop():
                log("🛑 중지 요청으로 시나리오를 중단합니다.")
                return False
            if not injector.is_alive():
                log("⚠️ 게임이 이미 종료되었습니다. 시나리오를 중단합니다.")
                return False
            time.sleep(1)
        return True

    log("=" * 50)
    log("🎬 결함 주입 시나리오 시작")
    log("=" * 50)

    if not wait(20, "정상 동작 구간"):
        return
    injector.hang(8)

    if not wait(12, "복구 확인 구간"):
        return
    injector.stutter(cycles=12)

    if not wait(15, "정상 동작 구간"):
        return
    injector.hang(12)

    if not wait(18, "최종 구간"):
        return
    injector.crash()

    log("=" * 50)
    log("🎬 시나리오 완료")
    log("=" * 50)


# ────────────────────────────────────────────────────────────
# 단독 실행용 CLI
# ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="게임 프로세스에 결함을 주입합니다 (QA 도구 검증용)")
    parser.add_argument("--target", type=str, default=None,
                        help='창 제목 일부. 예: "Maze Trials"')
    parser.add_argument("--pid", type=int, default=None,
                        help="프로세스 ID를 직접 지정")
    parser.add_argument("--scenario",
                        choices=["demo", "hang", "stutter", "crash"],
                        default="demo", help="실행할 시나리오 (기본: demo)")
    parser.add_argument("--duration", type=float, default=8.0,
                        help="hang 시나리오의 정지 시간(초). 기본 8초")
    args = parser.parse_args()

    pid = args.pid
    if pid is None:
        if not args.target:
            parser.error("--target 또는 --pid 중 하나는 반드시 필요합니다.")
        pid = find_process_by_window_title(args.target)
        if pid is None:
            print(f"❌ '{args.target}' 창을 찾을 수 없습니다.")
            print("   게임이 실행 중인지 확인하세요.")
            return 1

    try:
        # with문을 쓰므로 어떻게 끝나든 게임은 반드시 복구된다
        with FaultInjector(pid) as injector:
            if args.scenario == "demo":
                run_demo_scenario(injector)
            elif args.scenario == "hang":
                injector.hang(args.duration)
            elif args.scenario == "stutter":
                injector.stutter()
            elif args.scenario == "crash":
                injector.crash()

    except psutil.NoSuchProcess:
        print(f"❌ pid={pid} 프로세스가 존재하지 않습니다.")
        return 1
    except psutil.AccessDenied:
        print(f"❌ pid={pid} 접근 권한이 없습니다.")
        print("   관리자 권한으로 실행해보세요.")
        return 1
    except KeyboardInterrupt:
        print("\n⏹ 사용자가 중단했습니다. (게임은 복구됨)")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
