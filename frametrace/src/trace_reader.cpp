// frametrace - src/trace_reader.cpp

#include "frametrace/trace_reader.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <map>
#include <unordered_map>

namespace ft {
namespace {

const std::string kUnknown = "<unknown>";

struct StackEntry {
    std::uint64_t start_raw;
    std::uint32_t name_id;
};

}  // namespace

const std::string& Trace::name_of(std::uint32_t id) const {
    if (id < strings_.size()) return strings_[id];
    return kUnknown;
}

bool Trace::load(const char* path, std::string& error) {
    std::FILE* f = std::fopen(path, "rb");
    if (!f) {
        error = "파일을 열 수 없다: ";
        error += path;
        return false;
    }

    FileHeader header{};
    if (std::fread(&header, sizeof(header), 1, f) != 1) {
        error = "헤더를 읽을 수 없다. 파일이 잘렸다.";
        std::fclose(f);
        return false;
    }

    if (header.magic != kTraceMagic) {
        error = ".ftrace 파일이 아니다 (매직 불일치)";
        std::fclose(f);
        return false;
    }

    if (header.version != kTraceVersion) {
        char buf[128];
        std::snprintf(buf, sizeof(buf),
                      "포맷 버전 불일치: 파일=%u, 지원=%u", header.version,
                      kTraceVersion);
        error = buf;
        std::fclose(f);
        return false;
    }

    dropped_ = header.dropped_events;
    event_count_ = header.event_count;

    const double ticks_per_ns =
        header.ticks_per_ns > 0.0 ? header.ticks_per_ns : 1.0;

    // 이벤트를 읽는다.
    std::vector<Event> events(header.event_count);
    if (header.event_count > 0) {
        const std::size_t got =
            std::fread(events.data(), sizeof(Event), header.event_count, f);
        if (got != header.event_count) {
            error = "이벤트 데이터가 잘렸다.";
            std::fclose(f);
            return false;
        }
    }

    // 문자열 테이블.
    std::fseek(f, static_cast<long>(header.string_table_offset), SEEK_SET);
    strings_.reserve(header.string_count);
    for (std::uint32_t i = 0; i < header.string_count; ++i) {
        std::uint16_t len = 0;
        if (std::fread(&len, sizeof(len), 1, f) != 1) {
            error = "문자열 테이블이 잘렸다.";
            std::fclose(f);
            return false;
        }
        std::string s(len, '\0');
        if (len > 0 && std::fread(&s[0], 1, len, f) != len) {
            error = "문자열 테이블이 잘렸다.";
            std::fclose(f);
            return false;
        }
        strings_.push_back(std::move(s));
    }

    // 스레드 이름 ID 배열.
    std::vector<std::uint32_t> thread_name_ids(header.thread_count, 0xFFFFFFFFu);
    for (std::uint32_t i = 0; i < header.thread_count; ++i) {
        std::uint32_t nid = 0xFFFFFFFFu;
        if (std::fread(&nid, sizeof(nid), 1, f) != 1) break;
        thread_name_ids[i] = nid;
    }

    std::fclose(f);

    // -----------------------------------------------------------------------
    // 스팬 복원
    // -----------------------------------------------------------------------
    //
    // 이벤트는 스레드별로는 시간순이지만 파일 전체로는 섞여 있다.
    // 플러시 스레드가 스레드를 하나씩 돌면서 쓰기 때문이다.
    // 스레드마다 독립된 스택을 쓰므로 전역 정렬은 필요 없다.

    std::unordered_map<std::uint16_t, std::vector<StackEntry>> stacks;
    std::unordered_map<std::uint16_t, std::uint16_t> max_depth;
    std::unordered_map<std::uint16_t, std::size_t> span_counts;

    std::uint64_t base = header.base_timestamp;
    std::uint64_t max_raw = base;

    auto to_ns = [&](std::uint64_t raw) -> std::uint64_t {
        if (raw < base) return 0;
        return static_cast<std::uint64_t>(static_cast<double>(raw - base) /
                                          ticks_per_ns);
    };

    for (const Event& ev : events) {
        if (ev.timestamp > max_raw) max_raw = ev.timestamp;

        switch (static_cast<EventType>(ev.type)) {
            case EventType::ScopeBegin: {
                stacks[ev.thread_id].push_back({ev.timestamp, ev.name_id});
                break;
            }
            case EventType::ScopeEnd: {
                auto& stack = stacks[ev.thread_id];
                if (stack.empty()) {
                    // 짝 없는 End. 드롭된 Begin이 있었다는 뜻.
                    ++orphan_ends_;
                    break;
                }
                const StackEntry entry = stack.back();
                stack.pop_back();

                Span span;
                span.start_ns = to_ns(entry.start_raw);
                span.end_ns = to_ns(ev.timestamp);
                span.name_id = entry.name_id;
                span.thread_id = ev.thread_id;
                span.depth = static_cast<std::uint16_t>(stack.size());

                if (span.depth > max_depth[ev.thread_id]) {
                    max_depth[ev.thread_id] = span.depth;
                }
                ++span_counts[ev.thread_id];

                spans_.push_back(span);
                break;
            }
            case EventType::FrameMark: {
                frame_marks_.push_back(to_ns(ev.timestamp));
                break;
            }
            case EventType::Instant: {
                // 지속 시간 0인 스팬으로 취급한다.
                Span span;
                span.start_ns = to_ns(ev.timestamp);
                span.end_ns = span.start_ns;
                span.name_id = ev.name_id;
                span.thread_id = ev.thread_id;
                span.depth = static_cast<std::uint16_t>(
                    stacks[ev.thread_id].size());
                spans_.push_back(span);
                break;
            }
        }
    }

    // 스택에 남은 Begin은 End를 못 만난 것들이다.
    for (auto& kv : stacks) {
        unmatched_begins_ += kv.second.size();
    }

    duration_ns_ = to_ns(max_raw);

    // 스레드 정보 조립.
    for (std::uint32_t i = 0; i < header.thread_count; ++i) {
        ThreadInfo info;
        info.id = static_cast<std::uint16_t>(i);

        const std::uint32_t nid = thread_name_ids[i];
        if (nid != 0xFFFFFFFFu && nid < strings_.size()) {
            info.name = strings_[nid];
        } else {
            info.name = "thread " + std::to_string(i);
        }

        auto d = max_depth.find(static_cast<std::uint16_t>(i));
        info.max_depth = (d != max_depth.end()) ? d->second : 0;

        auto c = span_counts.find(static_cast<std::uint16_t>(i));
        info.span_count = (c != span_counts.end()) ? c->second : 0;

        threads_.push_back(info);
    }

    // 시작 시각 순으로 정렬한다. 뷰어가 순차 스캔으로 그릴 수 있게.
    std::sort(spans_.begin(), spans_.end(),
              [](const Span& a, const Span& b) {
                  if (a.thread_id != b.thread_id) return a.thread_id < b.thread_id;
                  if (a.start_ns != b.start_ns) return a.start_ns < b.start_ns;
                  return a.depth < b.depth;
              });

    std::sort(frame_marks_.begin(), frame_marks_.end());

    return true;
}

std::vector<ScopeStats> Trace::compute_scope_stats() const {
    std::map<std::uint32_t, ScopeStats> by_name;

    for (const Span& span : spans_) {
        ScopeStats& st = by_name[span.name_id];
        st.name_id = span.name_id;

        const std::uint64_t dur = span.duration_ns();
        ++st.call_count;
        st.total_ns += dur;
        if (dur < st.min_ns) st.min_ns = dur;
        if (dur > st.max_ns) st.max_ns = dur;
    }

    std::vector<ScopeStats> out;
    out.reserve(by_name.size());
    for (auto& kv : by_name) {
        kv.second.name = name_of(kv.first);
        if (kv.second.min_ns == UINT64_MAX) kv.second.min_ns = 0;
        out.push_back(kv.second);
    }

    std::sort(out.begin(), out.end(),
              [](const ScopeStats& a, const ScopeStats& b) {
                  return a.total_ns > b.total_ns;
              });

    return out;
}

}  // namespace ft
