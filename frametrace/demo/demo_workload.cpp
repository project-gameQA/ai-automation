// frametrace - demo/demo_workload.cpp
// 가짜 게임 루프. 트레이스에 볼 만한 게 담기도록 중첩된 스코프와
// 여러 스레드, 그리고 가끔 튀는 프레임(스파이크)을 만든다.
//
// 스파이크를 일부러 넣는 이유: 프로파일러의 존재 이유가 "가끔 튀는 프레임"을
// 찾는 것이기 때문이다. 평평한 트레이스는 뷰어를 검증하지 못한다.

#include <atomic>
#include <cmath>
#include <cstdio>
#include <random>
#include <thread>
#include <vector>

#include "frametrace/frametrace.h"

namespace {

// 최적화로 사라지지 않도록 결과를 흘려보낸다.
//
// std::atomic<double>::fetch_add는 C++20부터다. C++17을 유지하려고
// volatile을 쓴다. 여러 스레드가 동시에 쓰지만 값은 버릴 거라서 상관없다.
// 목적은 오로지 컴파일러가 burn()을 통째로 지우지 못하게 하는 것이다.
volatile double g_sink = 0.0;

void burn(int iterations) {
    double acc = 0.0;
    for (int i = 0; i < iterations; ++i) {
        acc += std::sin(static_cast<double>(i) * 0.001);
    }
    g_sink = g_sink + acc;
}

void narrow_phase(int pairs) {
    FT_SCOPE("narrow_phase");
    burn(pairs * 12);
}

void broad_phase() {
    FT_SCOPE("broad_phase");
    burn(400);
}

void physics_step(std::mt19937& rng) {
    FT_SCOPE("physics_step");

    broad_phase();

    std::uniform_int_distribution<int> dist(4, 14);
    const int pairs = dist(rng);
    for (int i = 0; i < pairs; ++i) {
        narrow_phase(pairs);
    }
}

void update_ai(std::mt19937& rng) {
    FT_SCOPE("update_ai");

    std::uniform_int_distribution<int> dist(2, 6);
    const int agents = dist(rng);
    for (int i = 0; i < agents; ++i) {
        FT_SCOPE("agent_think");
        burn(150);
    }
}

void cull() {
    FT_SCOPE("cull");
    burn(700);
}

void submit_draws(int count) {
    FT_SCOPE("submit_draws");
    for (int i = 0; i < count; ++i) {
        FT_SCOPE("draw_call");
        burn(20);
    }
}

void render(std::mt19937& rng) {
    FT_SCOPE("render");
    cull();

    std::uniform_int_distribution<int> dist(8, 20);
    submit_draws(dist(rng));
}

// 워커 스레드. 백그라운드 잡을 돌린다.
void worker_main(int index, std::atomic<bool>& running) {
    char name[32];
    std::snprintf(name, sizeof(name), "worker_%d", index);

    // 주의: name은 스택 배열이다. intern은 포인터로 캐싱하므로
    // 스레드마다 다른 주소가 되어 중복 항목이 생긴다. 여기서는
    // 스레드당 한 번이라 상관없다. 핫 패스에서는 리터럴만 써야 한다.
    FT_THREAD_NAME(name);

    std::mt19937 rng(static_cast<unsigned>(index * 7919));

    while (running.load(std::memory_order_relaxed)) {
        FT_SCOPE("job_batch");
        {
            FT_SCOPE("decompress");
            burn(500);
        }
        {
            FT_SCOPE("upload");
            burn(200);
        }
        std::this_thread::sleep_for(std::chrono::microseconds(500));
    }
}

}  // namespace

int main(int argc, char** argv) {
    const char* out = (argc > 1) ? argv[1] : "demo.ftrace";
    const int frames = (argc > 2) ? std::atoi(argv[2]) : 300;
    const int worker_count = (argc > 3) ? std::atoi(argv[3]) : 2;

    if (!ft::Profiler::begin_session(out)) {
        std::fprintf(stderr, "세션 시작 실패: %s\n", out);
        return 1;
    }

    FT_THREAD_NAME("main");

    std::atomic<bool> running{true};
    std::vector<std::thread> workers;
    for (int i = 0; i < worker_count; ++i) {
        workers.emplace_back(worker_main, i, std::ref(running));
    }

    std::mt19937 rng(1234);
    std::uniform_int_distribution<int> spike_dist(0, 99);

    for (int frame = 0; frame < frames; ++frame) {
        {
            FT_SCOPE("frame");

            update_ai(rng);
            physics_step(rng);
            render(rng);

            // 20프레임마다 히칭을 하나 심는다. 뷰어에서 이게 눈에
            // 띄어야 프로파일러가 제 역할을 하는 것이다.
            if (frame % 20 == 19) {
                FT_SCOPE("asset_stream_hitch");
                burn(9000);
            }

            if (spike_dist(rng) < 3) {
                FT_SCOPE("gc_spike");
                burn(4000);
            }
        }

        FT_FRAME_MARK();
    }

    running.store(false);
    for (std::thread& t : workers) {
        t.join();
    }

    const ft::SessionStats stats = ft::Profiler::end_session();

    std::printf("트레이스 기록 완료: %s\n", out);
    std::printf("  이벤트      : %llu\n",
                static_cast<unsigned long long>(stats.events_written));
    std::printf("  드롭        : %llu\n",
                static_cast<unsigned long long>(stats.events_dropped));
    std::printf("  스레드      : %u\n", stats.thread_count);
    std::printf("  문자열      : %u\n", stats.string_count);
    std::printf("  ticks/ns    : %.4f\n", stats.ticks_per_ns);

    return 0;
}
