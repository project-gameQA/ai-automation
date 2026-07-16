// frametrace - tests/test_ring_buffer.cpp
// 링 버퍼 검증. 외부 테스트 프레임워크를 안 쓴다. 의존성 하나 늘리는 것보다
// assert 몇 줄이 낫다.
//
// 락프리 자료구조는 "돌아가는 것처럼 보이는" 상태가 제일 위험하다.
// 단일 스레드 테스트는 메모리 순서 버그를 절대 못 잡는다. 그래서
// 실제 스레드 두 개로 수백만 개를 밀어넣는 스트레스 테스트가 있어야 한다.

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <thread>
#include <vector>

#include "frametrace/detail/ring_buffer.h"

namespace {

int g_failures = 0;

void check(bool cond, const char* what) {
    if (!cond) {
        std::printf("  [실패] %s\n", what);
        ++g_failures;
    } else {
        std::printf("  [통과] %s\n", what);
    }
}

void test_basic() {
    std::printf("\ntest_basic\n");

    ft::detail::SpscRing<int, 8> ring;
    check(ring.size_approx() == 0, "처음엔 비어 있다");

    check(ring.push(1), "push 성공");
    check(ring.size_approx() == 1, "크기 1");

    int out[8];
    check(ring.pop_bulk(out, 8) == 1, "하나 꺼냄");
    check(out[0] == 1, "값 보존");
    check(ring.size_approx() == 0, "다시 비었다");
}

void test_full() {
    std::printf("\ntest_full\n");

    ft::detail::SpscRing<int, 4> ring;

    for (int i = 0; i < 4; ++i) {
        check(ring.push(i), "용량까지 push 성공");
    }

    check(!ring.push(99), "가득 차면 push 실패");
    check(ring.size_approx() == 4, "크기가 용량과 같다");

    int out[4];
    check(ring.pop_bulk(out, 4) == 4, "전부 꺼냄");
    check(out[0] == 0 && out[3] == 3, "FIFO 순서 유지");

    check(ring.push(42), "비운 뒤 다시 push 가능");
}

void test_wraparound() {
    std::printf("\ntest_wraparound\n");

    ft::detail::SpscRing<int, 4> ring;
    int out[4];

    // 용량의 몇 배를 통과시켜서 인덱스가 여러 번 감기게 한다.
    // 마스크 계산이 틀리면 여기서 터진다.
    bool ok = true;
    for (int round = 0; round < 100; ++round) {
        for (int i = 0; i < 3; ++i) {
            if (!ring.push(round * 3 + i)) ok = false;
        }
        const std::size_t n = ring.pop_bulk(out, 4);
        if (n != 3) ok = false;
        for (int i = 0; i < 3; ++i) {
            if (out[i] != round * 3 + i) ok = false;
        }
    }
    check(ok, "300회 랩어라운드 후에도 순서와 값이 정확하다");
}

void test_partial_drain() {
    std::printf("\ntest_partial_drain\n");

    ft::detail::SpscRing<int, 8> ring;
    for (int i = 0; i < 6; ++i) ring.push(i);

    int out[3];
    check(ring.pop_bulk(out, 3) == 3, "요청한 만큼만 꺼낸다");
    check(out[0] == 0 && out[2] == 2, "앞에서부터 꺼낸다");
    check(ring.size_approx() == 3, "나머지는 남아 있다");

    int rest[8];
    check(ring.pop_bulk(rest, 8) == 3, "남은 걸 마저 꺼낸다");
    check(rest[0] == 3, "이어서 꺼낸다");
}

// 진짜 테스트. 두 스레드로 스트레스.
void test_concurrent() {
    std::printf("\ntest_concurrent\n");

    constexpr std::size_t kCapacity = 1024;
    constexpr std::uint64_t kCount = 2000000;

    ft::detail::SpscRing<std::uint64_t, kCapacity> ring;

    std::atomic<std::uint64_t> produced{0};
    std::atomic<std::uint64_t> consumed{0};
    std::atomic<bool> order_ok{true};
    std::atomic<bool> done_producing{false};

    std::thread producer([&] {
        std::uint64_t i = 0;
        while (i < kCount) {
            // 값 자체를 시퀀스 번호로 쓴다. 소비자가 순서를 검증할 수 있다.
            if (ring.push(i)) {
                ++i;
                produced.fetch_add(1, std::memory_order_relaxed);
            }
            // push 실패하면 다시 시도한다. 절대 블로킹하지 않는다.
        }
        done_producing.store(true, std::memory_order_release);
    });

    std::thread consumer([&] {
        std::vector<std::uint64_t> buf(256);
        std::uint64_t expected = 0;

        while (true) {
            const std::size_t n = ring.pop_bulk(buf.data(), buf.size());
            for (std::size_t k = 0; k < n; ++k) {
                if (buf[k] != expected) {
                    order_ok.store(false, std::memory_order_relaxed);
                }
                ++expected;
            }
            consumed.fetch_add(n, std::memory_order_relaxed);

            if (n == 0 && done_producing.load(std::memory_order_acquire)) {
                // 생산자가 끝났고 링이 비었으면 종료.
                if (ring.size_approx() == 0) break;
            }
        }
    });

    producer.join();
    consumer.join();

    std::printf("  생산 %llu / 소비 %llu\n",
                static_cast<unsigned long long>(produced.load()),
                static_cast<unsigned long long>(consumed.load()));

    check(produced.load() == kCount, "전부 생산됨");
    check(consumed.load() == kCount, "손실 없이 전부 소비됨");
    check(order_ok.load(), "FIFO 순서가 정확히 유지됨");
}

void test_no_false_sharing_layout() {
    std::printf("\ntest_no_false_sharing_layout\n");

    // 구조체 레이아웃 자체를 검증한다. 누군가 alignas를 지우면
    // 성능이 조용히 반토막 나는데, 그건 테스트로 안 잡힌다.
    // 그래서 레이아웃을 직접 확인한다.
    using Ring = ft::detail::SpscRing<std::uint64_t, 64>;

    check(sizeof(Ring) >= 3 * ft::detail::kCacheLine,
          "생산자/소비자/데이터가 각각 다른 캐시 라인에 있다");
    check(alignof(Ring) == ft::detail::kCacheLine,
          "링 자체가 캐시 라인에 정렬됨");
}

}  // namespace

int main() {
    std::printf("=== SpscRing 테스트 ===\n");

    test_basic();
    test_full();
    test_wraparound();
    test_partial_drain();
    test_no_false_sharing_layout();
    test_concurrent();

    std::printf("\n");
    if (g_failures == 0) {
        std::printf("전부 통과\n");
        return 0;
    }
    std::printf("실패 %d개\n", g_failures);
    return 1;
}
