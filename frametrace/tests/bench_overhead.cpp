// frametrace - tests/bench_overhead.cpp
// 계측 오버헤드 측정.
//
// 프로파일러에서 이 숫자가 전부다. 오버헤드가 크면 측정 행위가 측정 대상을
// 바꿔버린다. 그러면 프로파일러가 아니라 거짓말 생성기다.
//
// 측정 항목:
//   1. 베이스라인 - 빈 루프
//   2. 세션 활성 - FT_SCOPE가 실제로 이벤트를 기록
//   3. 세션 비활성 - 매크로는 있지만 세션이 없음 (원자적 로드 후 리턴)
//   4. 컴파일 제거 - FT_ENABLED=0 (별도 바이너리, run_bench.sh 참고)
//
// 3번이 중요한 이유: 출시 빌드에서 계측을 남겨두고 런타임에 끄는 경우가 많다.
// 그때 비용이 얼마인지 알아야 한다.

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <thread>
#include <vector>

#include "frametrace/frametrace.h"

namespace {

// 컴파일러가 루프를 통째로 지우지 못하게 막는다.
volatile std::uint64_t g_sink = 0;

using Clock = std::chrono::steady_clock;

constexpr int kIterations = 2000000;
constexpr int kRepeats = 7;

// 최소값을 쓴다. 평균은 스케줄러 노이즈에 오염된다.
// 최소값은 "방해받지 않았을 때의 진짜 비용"에 가장 가깝다.
double bench_min(const std::vector<double>& samples) {
    return *std::min_element(samples.begin(), samples.end());
}

double measure_baseline() {
    std::vector<double> samples;

    for (int r = 0; r < kRepeats; ++r) {
        const auto start = Clock::now();
        for (int i = 0; i < kIterations; ++i) {
            g_sink = g_sink + 1;
        }
        const auto end = Clock::now();

        const double ns =
            std::chrono::duration_cast<std::chrono::nanoseconds>(end - start)
                .count();
        samples.push_back(ns / kIterations);
    }

    return bench_min(samples);
}

double measure_scoped() {
    std::vector<double> samples;

    for (int r = 0; r < kRepeats; ++r) {
        const auto start = Clock::now();
        for (int i = 0; i < kIterations; ++i) {
            FT_SCOPE("bench_scope");
            g_sink = g_sink + 1;
        }
        const auto end = Clock::now();

        const double ns =
            std::chrono::duration_cast<std::chrono::nanoseconds>(end - start)
                .count();
        samples.push_back(ns / kIterations);
    }

    return bench_min(samples);
}

// 버스트 방식 측정.
//
// 왜 필요한가:
//   위의 measure_scoped()를 세션 활성 상태로 돌리면 드롭률이 60%를 넘는다.
//   200만 스코프를 쉬지 않고 밀면 초당 4000만 이벤트가 나오는데, 링 용량은
//   16384개다. 플러시 스레드가 따라잡을 수가 없다.
//
//   문제는 드롭된 push가 성공한 push보다 싸다는 것이다. 버퍼 쓰기와
//   release store를 건너뛰기 때문이다. 드롭이 66%면 측정값의 대부분이
//   "실패 경로 비용"이고, 이건 우리가 알고 싶은 숫자가 아니다.
//   벤치마크가 오버헤드를 실제보다 낮게 보고하게 된다.
//
// 해결:
//   링 용량보다 작은 버스트로 나눠서 잰다. 버스트 사이에 플러시 스레드가
//   비울 시간을 준다. 시간은 버스트 구간만 잰다. 이러면 드롭 0이고
//   모든 push가 성공 경로를 탄다.
//
//   실제 게임도 이렇게 동작한다. 스코프 사이에 진짜 일이 있으므로
//   이벤트가 초당 4000만 개씩 나오지 않는다. 버스트 측정이 오히려
//   현실에 가깝다.
double measure_scoped_bursts(int burst_scopes, int burst_count) {
    std::vector<double> samples;

    for (int b = 0; b < burst_count; ++b) {
        const auto start = Clock::now();
        for (int i = 0; i < burst_scopes; ++i) {
            FT_SCOPE("burst_scope");
            g_sink = g_sink + 1;
        }
        const auto end = Clock::now();

        const double ns =
            std::chrono::duration_cast<std::chrono::nanoseconds>(end - start)
                .count();
        samples.push_back(ns / burst_scopes);

        // 플러시 스레드가 링을 비울 시간. 이 구간은 측정에 안 들어간다.
        std::this_thread::sleep_for(std::chrono::milliseconds(4));
    }

    return bench_min(samples);
}

void print_row(const char* label, double ns_per_iter, double baseline) {
    const double overhead = ns_per_iter - baseline;
    std::printf("  %-28s %8.2f ns   (오버헤드 %+7.2f ns)\n", label,
                ns_per_iter, overhead);
}

}  // namespace

int main() {
    std::printf("=== frametrace 오버헤드 벤치마크 ===\n");
    std::printf("반복 %d회 x %d세트, 최소값 채택\n\n", kIterations, kRepeats);

#if !FT_ENABLED
    std::printf("FT_ENABLED=0 으로 빌드됨. 매크로가 완전히 제거된 상태다.\n\n");
#endif

    // 1. 베이스라인
    const double baseline = measure_baseline();
    std::printf("루프 자체:\n");
    std::printf("  %-28s %8.2f ns\n\n", "빈 루프 (베이스라인)", baseline);

    // 2. 세션 비활성 상태
    const double inactive = measure_scoped();

    // 3. 세션 활성 - 버스트 방식 (드롭 없음, 이게 진짜 수치다)
    if (!ft::Profiler::begin_session("/tmp/bench_burst.ftrace")) {
        std::fprintf(stderr, "세션 시작 실패\n");
        return 1;
    }
    FT_THREAD_NAME("bench");

    // 버스트 크기는 링 용량(16384 이벤트 = 8192 스코프)보다 작아야 한다.
    const double active_burst = measure_scoped_bursts(4000, 40);
    const ft::SessionStats burst_stats = ft::Profiler::end_session();

    // 4. 세션 활성 - 포화 상태 (드롭이 나는 비현실적 조건)
    if (!ft::Profiler::begin_session("/tmp/bench_saturated.ftrace")) {
        std::fprintf(stderr, "세션 시작 실패\n");
        return 1;
    }
    FT_THREAD_NAME("bench");
    const double active_sat = measure_scoped();
    const ft::SessionStats sat_stats = ft::Profiler::end_session();

    // 결과
    std::printf("FT_SCOPE 1개 (= 이벤트 2개):\n");
    print_row("세션 비활성", inactive, baseline);
    print_row("세션 활성 (버스트, 드롭 0)", active_burst, baseline);
    print_row("세션 활성 (포화, 드롭 발생)", active_sat, baseline);

    const double per_scope = active_burst - baseline;
    const double per_event = per_scope / 2.0;

    std::printf("\n핵심 수치 (버스트 측정 기준):\n");
    std::printf("  스코프당 오버헤드      : %.2f ns\n", per_scope);
    std::printf("  이벤트당 오버헤드      : %.2f ns\n", per_event);
    std::printf("  비활성 시 오버헤드     : %.2f ns\n", inactive - baseline);

    auto drop_rate = [](const ft::SessionStats& s) -> double {
        const std::uint64_t total = s.events_written + s.events_dropped;
        return total ? 100.0 * static_cast<double>(s.events_dropped) /
                           static_cast<double>(total)
                     : 0.0;
    };

    std::printf("\n드롭률:\n");
    std::printf("  버스트 (링 용량 이하)  : %.2f%%  (이벤트 %llu)\n",
                drop_rate(burst_stats),
                static_cast<unsigned long long>(burst_stats.events_written));
    std::printf("  포화 (쉬지 않고 밀기)  : %.2f%%  (이벤트 %llu)\n",
                drop_rate(sat_stats),
                static_cast<unsigned long long>(sat_stats.events_written));

    if (drop_rate(sat_stats) > 5.0) {
        const double delta = active_sat - active_burst;
        std::printf(
            "\n  [해석] 포화 측정에서 push의 %.0f%%가 드롭됐다. 이 수치에는\n"
            "         반대 방향의 힘 두 개가 섞여 있다.\n"
            "           - 낮추는 쪽: 드롭 경로는 버퍼 쓰기와 release store를\n"
            "             건너뛴다. 성공 경로보다 싸다.\n"
            "           - 올리는 쪽: 플러시 스레드가 쉬지 않고 돌면서 CPU와\n"
            "             캐시를 놓고 경합한다. 코어가 적을수록 심해진다.\n"
            "         이 머신에서는 포화가 버스트보다 %+.2f ns 나왔다. 즉 %s.\n"
            "         어느 쪽이 이기든 드롭 %.0f%%짜리 트레이스는 구멍투성이라\n"
            "         쓸모가 없다. 버스트 수치를 봐야 한다.\n",
            drop_rate(sat_stats), delta,
            delta > 0 ? "경합이 이겼다" : "드롭 경로가 이겼다",
            drop_rate(sat_stats));
    }
    if (drop_rate(burst_stats) > 1.0) {
        std::printf(
            "\n  [경고] 버스트 측정에서도 드롭이 났다. 이 머신에서는 플러시\n"
            "         스레드가 밀린다. 코어 수를 확인해야 한다.\n");
    }

    // 실전에서 무슨 뜻인지 환산해준다. ns 숫자만 보면 감이 안 온다.
    std::printf("\n실전 환산 (60 FPS = 프레임당 16.67ms):\n");
    for (int scopes : {1000, 10000, 50000}) {
        const double cost_ms = scopes * per_scope / 1e6;
        std::printf("  프레임당 스코프 %-6d : %.4f ms  (프레임 예산의 %.2f%%)\n",
                    scopes, cost_ms, 100.0 * cost_ms / 16.67);
    }

    std::printf(
        "\n주의: 이 수치는 이 머신 기준이다. TSC 주파수, 캐시, 코어 수에\n"
        "      따라 달라진다. 자기 환경에서 다시 재야 한다.\n");

    return 0;
}
