// frametrace - trace_reader.h
// .ftrace 파일을 읽어서 스팬(span) 트리로 복원한다.
//
// 이벤트 스트림은 Begin/End가 번갈아 나오는 평평한 목록이다. 플레임 그래프를
// 그리려면 이걸 "시작 시각, 끝 시각, 중첩 깊이"를 가진 스팬으로 바꿔야 한다.
// 스레드마다 스택을 하나씩 두고 Begin에 push, End에 pop하면 된다.
// pop할 때 스택 깊이가 그대로 중첩 깊이다.

#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "frametrace/trace_format.h"

namespace ft {

// 복원된 스팬 하나. 플레임 그래프의 사각형 하나에 대응한다.
struct Span {
    std::uint64_t start_ns = 0;  // base_timestamp 기준 상대 나노초
    std::uint64_t end_ns = 0;
    std::uint32_t name_id = 0;
    std::uint16_t thread_id = 0;
    std::uint16_t depth = 0;  // 중첩 깊이, 0이 최상위

    std::uint64_t duration_ns() const { return end_ns - start_ns; }
};

struct ThreadInfo {
    std::uint16_t id = 0;
    std::string name;
    std::uint16_t max_depth = 0;
    std::size_t span_count = 0;
};

// 스코프 이름별 집계.
struct ScopeStats {
    std::uint32_t name_id = 0;
    std::string name;
    std::uint64_t call_count = 0;
    std::uint64_t total_ns = 0;
    std::uint64_t min_ns = UINT64_MAX;
    std::uint64_t max_ns = 0;

    double mean_ns() const {
        return call_count ? static_cast<double>(total_ns) /
                                static_cast<double>(call_count)
                          : 0.0;
    }
};

class Trace {
public:
    // 실패 시 false, error에 사유를 채운다.
    bool load(const char* path, std::string& error);

    const std::vector<Span>& spans() const { return spans_; }
    const std::vector<ThreadInfo>& threads() const { return threads_; }
    const std::vector<std::string>& strings() const { return strings_; }
    const std::vector<std::uint64_t>& frame_marks_ns() const {
        return frame_marks_;
    }

    const std::string& name_of(std::uint32_t id) const;

    std::uint64_t duration_ns() const { return duration_ns_; }
    std::uint64_t dropped_events() const { return dropped_; }
    std::uint64_t event_count() const { return event_count_; }
    std::uint64_t unmatched_begins() const { return unmatched_begins_; }
    std::uint64_t orphan_ends() const { return orphan_ends_; }

    // 스코프 이름별 집계를 total_ns 내림차순으로 반환한다.
    std::vector<ScopeStats> compute_scope_stats() const;

private:
    std::vector<Span> spans_;
    std::vector<ThreadInfo> threads_;
    std::vector<std::string> strings_;
    std::vector<std::uint64_t> frame_marks_;

    std::uint64_t duration_ns_ = 0;
    std::uint64_t dropped_ = 0;
    std::uint64_t event_count_ = 0;

    // 트레이스 무결성 지표. 링이 가득 차서 이벤트가 버려지면
    // 짝이 안 맞는 Begin/End가 생긴다.
    std::uint64_t unmatched_begins_ = 0;
    std::uint64_t orphan_ends_ = 0;
};

}  // namespace ft
