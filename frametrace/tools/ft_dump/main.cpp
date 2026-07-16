// frametrace - tools/ft_dump/main.cpp
// .ftrace 파일을 텍스트로 덤프한다.
//
// 뷰어(GUI)와 별개로 CLI가 있는 이유:
//   1. CI에서 돌릴 수 있다. GUI는 헤드리스 환경에서 못 돈다.
//   2. 포맷을 검증하는 가장 빠른 방법이다. 뷰어가 이상하면 먼저 여기를 본다.
//   3. Chrome Trace Viewer(chrome://tracing) JSON으로 내보낼 수 있다.
//      뷰어를 아직 안 만들었어도 이걸로 볼 수 있다.

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "frametrace/trace_reader.h"

namespace {

void print_usage() {
    std::printf(
        "사용법: ft_dump <파일.ftrace> [옵션]\n"
        "\n"
        "옵션:\n"
        "  --summary       요약만 출력 (기본)\n"
        "  --stats         스코프별 집계 테이블\n"
        "  --spans [N]     스팬을 최대 N개 출력 (기본 50)\n"
        "  --frames        프레임 시간 목록과 이상치\n"
        "  --json <경로>   Chrome Trace Format으로 내보내기\n"
        "                  chrome://tracing 에 드래그하면 바로 보인다\n");
}

std::string fmt_ns(std::uint64_t ns) {
    char buf[64];
    if (ns < 1000) {
        std::snprintf(buf, sizeof(buf), "%llu ns",
                      static_cast<unsigned long long>(ns));
    } else if (ns < 1000000) {
        std::snprintf(buf, sizeof(buf), "%.2f us", ns / 1e3);
    } else {
        std::snprintf(buf, sizeof(buf), "%.3f ms", ns / 1e6);
    }
    return buf;
}

void print_summary(const ft::Trace& trace) {
    std::printf("== 요약 ==\n");
    std::printf("  총 시간   : %s\n", fmt_ns(trace.duration_ns()).c_str());
    std::printf("  이벤트    : %llu\n",
                static_cast<unsigned long long>(trace.event_count()));
    std::printf("  스팬      : %zu\n", trace.spans().size());
    std::printf("  프레임    : %zu\n", trace.frame_marks_ns().size());
    std::printf("  스레드    : %zu\n", trace.threads().size());
    std::printf("  스코프명  : %zu\n", trace.strings().size());

    if (trace.dropped_events() > 0) {
        std::printf("\n  [경고] 이벤트 %llu개가 드롭됐다. 링 버퍼가 가득 찼다.\n",
                    static_cast<unsigned long long>(trace.dropped_events()));
        std::printf("         트레이스에 구멍이 있다. kRingCapacity를 키우거나\n");
        std::printf("         플러시 주기를 줄여야 한다.\n");
    }
    if (trace.unmatched_begins() > 0 || trace.orphan_ends() > 0) {
        std::printf("\n  [경고] 짝이 안 맞는 이벤트: begin=%llu, end=%llu\n",
                    static_cast<unsigned long long>(trace.unmatched_begins()),
                    static_cast<unsigned long long>(trace.orphan_ends()));
    }

    std::printf("\n  스레드:\n");
    for (const ft::ThreadInfo& t : trace.threads()) {
        std::printf("    [%2u] %-16s 스팬 %-8zu 최대깊이 %u\n", t.id,
                    t.name.c_str(), t.span_count, t.max_depth);
    }
}

void print_stats(const ft::Trace& trace) {
    const auto stats = trace.compute_scope_stats();

    std::printf("\n== 스코프별 집계 (총 시간 내림차순) ==\n");
    std::printf("%-24s %10s %12s %12s %12s %12s\n", "이름", "호출", "총합",
                "평균", "최소", "최대");
    std::printf("%s\n", std::string(88, '-').c_str());

    for (const ft::ScopeStats& s : stats) {
        std::printf("%-24s %10llu %12s %12s %12s %12s\n", s.name.c_str(),
                    static_cast<unsigned long long>(s.call_count),
                    fmt_ns(s.total_ns).c_str(),
                    fmt_ns(static_cast<std::uint64_t>(s.mean_ns())).c_str(),
                    fmt_ns(s.min_ns).c_str(), fmt_ns(s.max_ns).c_str());
    }
}

void print_frames(const ft::Trace& trace) {
    const auto& marks = trace.frame_marks_ns();
    if (marks.size() < 2) {
        std::printf("\n프레임 마크가 부족하다.\n");
        return;
    }

    std::vector<std::uint64_t> deltas;
    deltas.reserve(marks.size() - 1);
    for (std::size_t i = 1; i < marks.size(); ++i) {
        deltas.push_back(marks[i] - marks[i - 1]);
    }

    std::uint64_t sum = 0;
    std::uint64_t worst = 0;
    std::size_t worst_idx = 0;
    for (std::size_t i = 0; i < deltas.size(); ++i) {
        sum += deltas[i];
        if (deltas[i] > worst) {
            worst = deltas[i];
            worst_idx = i;
        }
    }

    const double mean = static_cast<double>(sum) / deltas.size();

    std::vector<std::uint64_t> sorted = deltas;
    std::sort(sorted.begin(), sorted.end());
    const std::uint64_t p50 = sorted[sorted.size() / 2];
    const std::uint64_t p99 = sorted[static_cast<std::size_t>(
        sorted.size() * 0.99)];

    std::printf("\n== 프레임 시간 ==\n");
    std::printf("  프레임 수 : %zu\n", deltas.size());
    std::printf("  평균      : %s (%.1f FPS)\n",
                fmt_ns(static_cast<std::uint64_t>(mean)).c_str(),
                1e9 / mean);
    std::printf("  p50       : %s\n", fmt_ns(p50).c_str());
    std::printf("  p99       : %s\n", fmt_ns(p99).c_str());
    std::printf("  최악      : %s (프레임 #%zu)\n", fmt_ns(worst).c_str(),
                worst_idx);

    // p99가 p50의 2배를 넘으면 히칭이 있다는 뜻이다.
    // 평균 FPS만 보면 절대 안 보이는 문제다.
    if (p99 > p50 * 2) {
        std::printf(
            "\n  [주목] p99가 p50의 %.1f배다. 평균은 멀쩡한데 히칭이 있다.\n",
            static_cast<double>(p99) / static_cast<double>(p50));
    }

    std::printf("\n  p50의 2배를 넘는 프레임:\n");
    int shown = 0;
    for (std::size_t i = 0; i < deltas.size() && shown < 20; ++i) {
        if (deltas[i] > p50 * 2) {
            std::printf("    #%-5zu %s\n", i, fmt_ns(deltas[i]).c_str());
            ++shown;
        }
    }
    if (shown == 0) {
        std::printf("    없음\n");
    }
}

void print_spans(const ft::Trace& trace, int limit) {
    std::printf("\n== 스팬 (최대 %d개) ==\n", limit);
    int shown = 0;
    for (const ft::Span& s : trace.spans()) {
        if (shown >= limit) break;
        std::printf("  [t%u] %*s%-20s %10s  @%s\n", s.thread_id, s.depth * 2,
                    "", trace.name_of(s.name_id).c_str(),
                    fmt_ns(s.duration_ns()).c_str(),
                    fmt_ns(s.start_ns).c_str());
        ++shown;
    }
}

// Chrome Trace Format으로 내보낸다.
// 뷰어를 직접 만들기 전에도 chrome://tracing 으로 볼 수 있게 하는 탈출구다.
bool export_json(const ft::Trace& trace, const char* path) {
    std::FILE* f = std::fopen(path, "wb");
    if (!f) return false;

    std::fprintf(f, "{\"traceEvents\":[\n");

    bool first = true;
    for (const ft::ThreadInfo& t : trace.threads()) {
        if (!first) std::fprintf(f, ",\n");
        first = false;
        std::fprintf(f,
                     "{\"name\":\"thread_name\",\"ph\":\"M\",\"pid\":1,"
                     "\"tid\":%u,\"args\":{\"name\":\"%s\"}}",
                     t.id, t.name.c_str());
    }

    for (const ft::Span& s : trace.spans()) {
        if (!first) std::fprintf(f, ",\n");
        first = false;
        // Chrome 포맷은 마이크로초 단위다.
        std::fprintf(f,
                     "{\"name\":\"%s\",\"ph\":\"X\",\"pid\":1,\"tid\":%u,"
                     "\"ts\":%.3f,\"dur\":%.3f}",
                     trace.name_of(s.name_id).c_str(), s.thread_id,
                     s.start_ns / 1000.0, s.duration_ns() / 1000.0);
    }

    std::fprintf(f, "\n]}\n");
    std::fclose(f);
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        print_usage();
        return 1;
    }

    ft::Trace trace;
    std::string error;
    if (!trace.load(argv[1], error)) {
        std::fprintf(stderr, "오류: %s\n", error.c_str());
        return 1;
    }

    bool did_something = false;

    for (int i = 2; i < argc; ++i) {
        if (std::strcmp(argv[i], "--stats") == 0) {
            print_stats(trace);
            did_something = true;
        } else if (std::strcmp(argv[i], "--frames") == 0) {
            print_frames(trace);
            did_something = true;
        } else if (std::strcmp(argv[i], "--spans") == 0) {
            int limit = 50;
            if (i + 1 < argc && argv[i + 1][0] != '-') {
                limit = std::atoi(argv[++i]);
            }
            print_spans(trace, limit);
            did_something = true;
        } else if (std::strcmp(argv[i], "--json") == 0) {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "--json 뒤에 경로가 필요하다\n");
                return 1;
            }
            const char* out = argv[++i];
            if (!export_json(trace, out)) {
                std::fprintf(stderr, "JSON 쓰기 실패: %s\n", out);
                return 1;
            }
            std::printf("Chrome Trace Format으로 내보냈다: %s\n", out);
            std::printf("chrome://tracing 에 드래그해서 열면 된다.\n");
            did_something = true;
        } else if (std::strcmp(argv[i], "--summary") == 0) {
            print_summary(trace);
            did_something = true;
        }
    }

    if (!did_something) {
        print_summary(trace);
    }

    return 0;
}
