// frametrace - detail/clock.h
// 타임스탬프 소스.
//
// 설계 근거:
//   std::chrono::steady_clock::now()는 리눅스에서 보통 20~25ns 걸린다.
//   스코프 하나당 두 번(begin/end) 부르면 40~50ns다. 이건 측정하려는
//   함수 자체보다 비쌀 수 있다.
//
//   rdtsc는 20~30 사이클, 대략 7~10ns다. 대신 두 가지 문제가 있다.
//     1. 단위가 사이클이다. ns로 바꾸려면 주파수를 알아야 한다 -> 캘리브레이션.
//     2. 아웃오브오더 실행 때문에 rdtsc가 재배치될 수 있다.
//
//   2번은 lfence를 넣으면 막을 수 있지만 그러면 다시 느려진다. Tracy도
//   같은 선택을 하는데, 프로파일링 목적에서는 몇 사이클 흔들리는 것보다
//   오버헤드가 낮은 게 중요하다. 스코프가 수백 ns 이상이면 무시할 수준이다.
//   그래서 기본은 fence 없는 rdtsc이고, FT_SERIALIZE_TSC로 켤 수 있게 뒀다.
//
//   최신 x86은 invariant TSC를 지원한다. 즉 주파수 스케일링/절전 상태와
//   무관하게 TSC가 일정한 속도로 증가한다. 이게 없는 옛날 CPU에서는
//   TSC를 신뢰할 수 없으므로 steady_clock으로 폴백한다.

#pragma once

#include <chrono>
#include <cstdint>

#if defined(_MSC_VER)
#include <intrin.h>
#elif defined(__x86_64__) || defined(__i386__)
#include <x86intrin.h>
#endif

namespace ft {
namespace detail {

#if defined(FT_FORCE_CHRONO_CLOCK)
#define FT_USING_TSC 0
#elif defined(_MSC_VER) && (defined(_M_X64) || defined(_M_IX86))
#define FT_USING_TSC 1
#elif defined(__x86_64__) || defined(__i386__)
#define FT_USING_TSC 1
#else
#define FT_USING_TSC 0
#endif

// 원시 타임스탬프를 읽는다. 단위는 플랫폼에 따라 사이클 또는 나노초.
// 인라인이 강제되어야 한다. 함수 호출 오버헤드가 측정값의 상당 부분이 된다.
#if defined(_MSC_VER)
__forceinline
#else
__attribute__((always_inline)) inline
#endif
    std::uint64_t
    now_raw() noexcept {
#if FT_USING_TSC
#if defined(FT_SERIALIZE_TSC)
    // lfence는 이전 명령이 모두 완료될 때까지 rdtsc를 지연시킨다.
    // 정확도는 올라가지만 파이프라인이 비워지므로 느려진다.
    _mm_lfence();
    return __rdtsc();
#else
    return __rdtsc();
#endif
#else
    return static_cast<std::uint64_t>(
        std::chrono::steady_clock::now().time_since_epoch().count());
#endif
}

// TSC 틱을 나노초로 바꾸는 계수를 구한다.
// steady_clock을 기준으로 정해진 시간 동안 TSC가 얼마나 흘렀는지 재서
// 비율을 계산한다.
//
// 짧게 재면 부정확하고 길게 재면 프로그램 시작이 느려진다. 10ms면
// 대략 0.01% 오차 수준으로 충분하다.
inline double calibrate_ticks_per_ns() noexcept {
#if !FT_USING_TSC
    return 1.0;  // 이미 나노초 단위다
#else
    using Clock = std::chrono::steady_clock;

    // 캐시/분기 예측 워밍업. 첫 호출은 항상 느리다.
    for (int i = 0; i < 100; ++i) {
        (void)now_raw();
        (void)Clock::now();
    }

    const auto wall_start = Clock::now();
    const std::uint64_t tsc_start = now_raw();

    // 바쁜 대기. sleep을 쓰면 스케줄러 오차가 섞인다.
    const auto target = std::chrono::milliseconds(10);
    while (Clock::now() - wall_start < target) {
        // spin
    }

    const std::uint64_t tsc_end = now_raw();
    const auto wall_end = Clock::now();

    const auto elapsed_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(wall_end -
                                                             wall_start)
            .count();

    if (elapsed_ns <= 0) {
        return 1.0;
    }

    return static_cast<double>(tsc_end - tsc_start) /
           static_cast<double>(elapsed_ns);
#endif
}

}  // namespace detail
}  // namespace ft
