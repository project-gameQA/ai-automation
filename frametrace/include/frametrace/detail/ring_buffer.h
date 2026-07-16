// frametrace - detail/ring_buffer.h
// 단일 생산자 / 단일 소비자 락프리 링 버퍼.
//
// 설계 근거:
//   - 프로파일링 대상 스레드(생산자)는 절대 블로킹되면 안 된다. 락을 잡는 순간
//     측정하려던 대상의 타이밍 자체가 왜곡된다. 그래서 락프리 SPSC를 쓴다.
//   - 스레드마다 링을 하나씩 갖는다. 그래서 MPMC가 아니라 SPSC로 충분하다.
//     생산자는 그 스레드 자신, 소비자는 플러시 스레드 하나뿐이다.
//   - head/tail을 각각 다른 캐시 라인에 놓는다. 같은 라인에 있으면 생산자와
//     소비자가 서로의 캐시 라인을 계속 무효화시킨다(false sharing).
//   - 용량은 2의 거듭제곱으로 강제한다. 나머지 연산 대신 마스크를 쓰기 위함이다.
//
// 버퍼가 가득 차면 push는 실패하고 이벤트를 버린다. 이건 의도된 동작이다.
// 프로파일러가 게임을 멈춰세우는 것보다 이벤트 몇 개를 잃는 게 낫다.
// 버린 개수는 따로 세서 리포트한다.

#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <new>

namespace ft {
namespace detail {

// 하드웨어 캐시 라인 크기. x86-64는 64바이트다.
// std::hardware_destructive_interference_size는 컴파일러 지원이 들쭉날쭉해서
// 직접 정의한다.
constexpr std::size_t kCacheLine = 64;

template <typename T, std::size_t Capacity>
class SpscRing {
    static_assert((Capacity & (Capacity - 1)) == 0,
                  "Capacity must be a power of two");
    static_assert(Capacity >= 2, "Capacity must be at least 2");

public:
    SpscRing() = default;

    SpscRing(const SpscRing&) = delete;
    SpscRing& operator=(const SpscRing&) = delete;

    // 생산자 전용. 링이 가득 차면 false.
    // 핫 패스다. 여기 들어가는 명령어 하나하나가 프로파일러 오버헤드다.
    bool push(const T& value) noexcept {
        // head_는 나만 쓴다. 그래서 relaxed로 읽어도 된다.
        const std::uint64_t head = head_.load(std::memory_order_relaxed);

        // 캐시된 tail로 먼저 확인한다. 대부분의 경우 여기서 통과하므로
        // 소비자의 캐시 라인을 건드리지 않는다. 이게 핵심 최적화다.
        if (head - cached_tail_ >= Capacity) {
            // 캐시가 오래됐다. 이때만 실제 tail을 읽는다.
            cached_tail_ = tail_.load(std::memory_order_acquire);
            if (head - cached_tail_ >= Capacity) {
                return false;  // 진짜로 가득 참
            }
        }

        buffer_[head & kMask] = value;

        // release: 위의 버퍼 쓰기가 이 store보다 먼저 보이도록 보장한다.
        // 이게 없으면 소비자가 아직 안 쓰인 슬롯을 읽을 수 있다.
        head_.store(head + 1, std::memory_order_release);
        return true;
    }

    // 소비자 전용. 최대 max_count개를 out으로 옮기고 실제 개수를 반환한다.
    // 하나씩 pop하면 매번 원자적 연산이 필요하다. 벌크로 빼면
    // 원자적 연산 두 번으로 수천 개를 옮길 수 있다.
    std::size_t pop_bulk(T* out, std::size_t max_count) noexcept {
        const std::uint64_t tail = tail_.load(std::memory_order_relaxed);

        if (cached_head_ == tail) {
            cached_head_ = head_.load(std::memory_order_acquire);
            if (cached_head_ == tail) {
                return 0;  // 비어 있음
            }
        }

        std::size_t available = static_cast<std::size_t>(cached_head_ - tail);
        if (available > max_count) {
            available = max_count;
        }

        for (std::size_t i = 0; i < available; ++i) {
            out[i] = buffer_[(tail + i) & kMask];
        }

        tail_.store(tail + available, std::memory_order_release);
        return available;
    }

    // 대략적인 크기. 디버깅/통계용이며 정확하지 않아도 된다.
    std::size_t size_approx() const noexcept {
        const std::uint64_t head = head_.load(std::memory_order_acquire);
        const std::uint64_t tail = tail_.load(std::memory_order_acquire);
        return static_cast<std::size_t>(head - tail);
    }

    static constexpr std::size_t capacity() noexcept { return Capacity; }

private:
    static constexpr std::uint64_t kMask = Capacity - 1;

    // 생산자만 쓰는 것들을 한 캐시 라인에 모은다.
    alignas(kCacheLine) std::atomic<std::uint64_t> head_{0};
    std::uint64_t cached_tail_{0};

    // 소비자만 쓰는 것들을 다른 캐시 라인에.
    alignas(kCacheLine) std::atomic<std::uint64_t> tail_{0};
    std::uint64_t cached_head_{0};

    // 데이터는 또 다른 라인부터 시작.
    alignas(kCacheLine) T buffer_[Capacity];
};

}  // namespace detail
}  // namespace ft
