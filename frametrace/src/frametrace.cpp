// frametrace - src/frametrace.cpp

#include "frametrace/frametrace.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "frametrace/detail/ring_buffer.h"

namespace ft {
namespace {

// 스레드당 링 용량. 16384 * 16바이트 = 256KB.
//
// 이 값을 고르는 기준:
//   플러시 스레드는 1ms 주기로 돈다. 스레드 하나가 1ms 동안 링을
//   가득 채우려면 초당 1600만 이벤트를 내야 한다. 스코프 하나가
//   최소 수십 ns인 걸 감안하면 현실적으로 도달하기 어렵다.
//   즉 정상적인 사용에서는 드롭이 나지 않는다.
constexpr std::size_t kRingCapacity = 16384;

// 한 번에 링에서 빼내는 최대 개수.
constexpr std::size_t kDrainBatch = 4096;

using EventRing = detail::SpscRing<Event, kRingCapacity>;

// ---------------------------------------------------------------------------
// 스레드 컨텍스트
// ---------------------------------------------------------------------------
//
// 수명 문제:
//   thread_local 객체는 스레드가 끝나면 소멸된다. 그런데 플러시 스레드는
//   여전히 그 링을 가리키고 있을 수 있다. 소멸된 메모리를 읽으면 UB다.
//
//   해결: 링을 힙에 할당하고 전역 리스트가 소유한다. thread_local은
//   포인터만 들고 있다. 스레드가 죽으면 alive_ 플래그만 내린다.
//   실제 해제는 end_session()에서 플러시 스레드를 조인한 뒤에 한다.
//
//   메모리를 좀 더 쓰지만 (스레드당 256KB), 프로파일러가 크래시를 내는 것보다
//   훨씬 낫다. 스레드를 수천 개 만드는 프로그램이라면 문제가 되겠지만
//   게임에서 그런 경우는 없다.

struct ThreadSlot {
    EventRing* ring = nullptr;
    std::atomic<bool> alive{false};
    std::uint32_t name_id = 0;
    bool has_name = false;
};

struct SessionState {
    std::mutex mutex;  // 등록/인터닝처럼 드문 작업만 보호한다. 핫 패스 아님.

    std::atomic<bool> active{false};

    std::vector<ThreadSlot*> slots;
    std::atomic<std::uint32_t> next_thread_id{0};

    std::unordered_map<const void*, std::uint32_t> intern_by_ptr;
    std::vector<std::string> strings;

    std::FILE* file = nullptr;
    std::thread flush_thread;
    std::atomic<bool> flush_stop{false};

    std::atomic<std::uint64_t> events_written{0};
    std::atomic<std::uint64_t> dropped{0};

    double ticks_per_ns = 1.0;
    std::uint64_t base_timestamp = 0;
};

SessionState& session() {
    static SessionState s;
    return s;
}

// 현재 스레드의 슬롯. 스레드마다 하나.
struct ThreadLocalHandle {
    ThreadSlot* slot = nullptr;
    std::uint16_t thread_id = 0;
    bool registered = false;

    ~ThreadLocalHandle() {
        // 스레드 종료. 링은 해제하지 않고 플래그만 내린다.
        if (slot) {
            slot->alive.store(false, std::memory_order_release);
        }
    }
};

thread_local ThreadLocalHandle tls_handle;

// 현재 스레드를 등록한다. 스레드당 딱 한 번 뮤텍스를 잡는다.
ThreadSlot* register_current_thread() {
    SessionState& s = session();

    std::lock_guard<std::mutex> lock(s.mutex);

    if (!s.active.load(std::memory_order_acquire)) {
        return nullptr;
    }

    auto* slot = new ThreadSlot();
    slot->ring = new EventRing();
    slot->alive.store(true, std::memory_order_release);

    const std::uint32_t id = s.next_thread_id.fetch_add(1);
    s.slots.push_back(slot);

    tls_handle.slot = slot;
    tls_handle.thread_id = static_cast<std::uint16_t>(id);
    tls_handle.registered = true;

    return slot;
}

// ---------------------------------------------------------------------------
// 플러시 스레드
// ---------------------------------------------------------------------------

void write_events(SessionState& s, const Event* events, std::size_t count) {
    if (count == 0) return;
    std::fwrite(events, sizeof(Event), count, s.file);
    s.events_written.fetch_add(count, std::memory_order_relaxed);
}

// 모든 스레드의 링을 한 바퀴 돌면서 비운다.
std::size_t drain_all(SessionState& s, std::vector<Event>& scratch) {
    std::size_t total = 0;

    // 슬롯 벡터는 등록 중에만 변경된다. 스냅샷을 뜬다.
    std::size_t slot_count;
    {
        std::lock_guard<std::mutex> lock(s.mutex);
        slot_count = s.slots.size();
    }

    for (std::size_t i = 0; i < slot_count; ++i) {
        ThreadSlot* slot;
        {
            std::lock_guard<std::mutex> lock(s.mutex);
            slot = s.slots[i];
        }
        if (!slot || !slot->ring) continue;

        for (;;) {
            const std::size_t n =
                slot->ring->pop_bulk(scratch.data(), kDrainBatch);
            if (n == 0) break;
            write_events(s, scratch.data(), n);
            total += n;
            if (n < kDrainBatch) break;
        }
    }

    return total;
}

void flush_loop() {
    SessionState& s = session();
    std::vector<Event> scratch(kDrainBatch);

    while (!s.flush_stop.load(std::memory_order_acquire)) {
        const std::size_t drained = drain_all(s, scratch);

        // 아무것도 없었으면 좀 쉬고, 바빴으면 바로 다시 돈다.
        if (drained == 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    }

    // 종료 전 마지막으로 남은 걸 전부 비운다.
    drain_all(s, scratch);
}

}  // namespace

// ---------------------------------------------------------------------------
// 공개 API 구현
// ---------------------------------------------------------------------------

bool Profiler::begin_session(const char* output_path) {
    SessionState& s = session();

    if (s.active.load(std::memory_order_acquire)) {
        return false;
    }

    std::FILE* f = std::fopen(output_path, "wb");
    if (!f) {
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(s.mutex);

        s.file = f;
        s.strings.clear();
        s.intern_by_ptr.clear();
        s.events_written.store(0);
        s.dropped.store(0);
        s.next_thread_id.store(0);
        s.flush_stop.store(false);

        s.ticks_per_ns = detail::calibrate_ticks_per_ns();

        // 헤더 자리를 미리 잡아둔다. 실제 값은 end_session에서 덮어쓴다.
        // 이벤트 개수나 문자열 오프셋은 지금 알 수 없다.
        FileHeader placeholder{};
        std::fwrite(&placeholder, sizeof(placeholder), 1, f);

        s.base_timestamp = detail::now_raw();
        s.active.store(true, std::memory_order_release);
    }

    s.flush_thread = std::thread(flush_loop);
    return true;
}

SessionStats Profiler::end_session() {
    SessionState& s = session();
    SessionStats out;

    if (!s.active.load(std::memory_order_acquire)) {
        return out;
    }

    // 새 이벤트를 막는다. 이 시점 이후 emit은 무시된다.
    s.active.store(false, std::memory_order_release);

    // 플러시 스레드를 세우고 조인한다. 조인 이후에는 아무도 링을
    // 건드리지 않으므로 안전하게 해제할 수 있다.
    s.flush_stop.store(true, std::memory_order_release);
    if (s.flush_thread.joinable()) {
        s.flush_thread.join();
    }

    std::lock_guard<std::mutex> lock(s.mutex);

    const std::uint64_t event_count = s.events_written.load();

    // 문자열 테이블을 파일 끝에 붙인다.
    const long table_offset = std::ftell(s.file);

    for (const std::string& str : s.strings) {
        const std::uint16_t len = static_cast<std::uint16_t>(
            str.size() > 0xFFFF ? 0xFFFF : str.size());
        std::fwrite(&len, sizeof(len), 1, s.file);
        std::fwrite(str.data(), 1, len, s.file);
    }

    // 스레드 이름 ID 배열.
    for (ThreadSlot* slot : s.slots) {
        const std::uint32_t nid = slot->has_name ? slot->name_id : 0xFFFFFFFFu;
        std::fwrite(&nid, sizeof(nid), 1, s.file);
    }

    // 헤더를 진짜 값으로 다시 쓴다.
    FileHeader header{};
    header.magic = kTraceMagic;
    header.version = kTraceVersion;
    header.header_size = sizeof(FileHeader);
    header.ticks_per_ns = s.ticks_per_ns;
    header.base_timestamp = s.base_timestamp;
    header.event_count = event_count;
    header.string_table_offset = static_cast<std::uint64_t>(table_offset);
    header.string_count = static_cast<std::uint32_t>(s.strings.size());
    header.dropped_events = s.dropped.load();
    header.thread_count = static_cast<std::uint32_t>(s.slots.size());

    std::fseek(s.file, 0, SEEK_SET);
    std::fwrite(&header, sizeof(header), 1, s.file);

    std::fclose(s.file);
    s.file = nullptr;

    out.events_written = event_count;
    out.events_dropped = header.dropped_events;
    out.thread_count = header.thread_count;
    out.string_count = header.string_count;
    out.ticks_per_ns = s.ticks_per_ns;

    // 이제 링을 해제해도 안전하다.
    for (ThreadSlot* slot : s.slots) {
        delete slot->ring;
        delete slot;
    }
    s.slots.clear();

    // 이 스레드의 tls 핸들은 이제 죽은 포인터를 들고 있다. 끊어준다.
    tls_handle.slot = nullptr;
    tls_handle.registered = false;

    return out;
}

bool Profiler::is_active() {
    return session().active.load(std::memory_order_acquire);
}

std::uint32_t Profiler::intern(const char* str) {
    SessionState& s = session();

    std::lock_guard<std::mutex> lock(s.mutex);

    // 문자열 리터럴은 포인터가 고유하다. 내용 비교 대신 포인터로 조회한다.
    // 같은 내용의 리터럴이 다른 주소에 있으면 중복 항목이 생기지만,
    // 정확성 문제는 없고 테이블이 몇 바이트 커질 뿐이다.
    auto it = s.intern_by_ptr.find(static_cast<const void*>(str));
    if (it != s.intern_by_ptr.end()) {
        return it->second;
    }

    const std::uint32_t id = static_cast<std::uint32_t>(s.strings.size());
    s.strings.emplace_back(str ? str : "");
    s.intern_by_ptr.emplace(static_cast<const void*>(str), id);
    return id;
}

void Profiler::name_current_thread(const char* name) {
    const std::uint32_t id = intern(name);

    SessionState& s = session();
    if (!s.active.load(std::memory_order_acquire)) return;

    ThreadSlot* slot = tls_handle.slot;
    if (!slot) {
        slot = register_current_thread();
        if (!slot) return;
    }

    std::lock_guard<std::mutex> lock(s.mutex);
    slot->name_id = id;
    slot->has_name = true;
}

void Profiler::emit(EventType type, std::uint32_t name_id) noexcept {
    SessionState& s = session();

    // 세션이 없으면 즉시 나간다. 원자적 로드 한 번.
    if (!s.active.load(std::memory_order_relaxed)) {
        return;
    }

    ThreadSlot* slot = tls_handle.slot;
    if (!slot) {
        // 이 스레드의 첫 이벤트. 여기서만 뮤텍스를 잡는다.
        slot = register_current_thread();
        if (!slot) return;
    }

    Event ev;
    ev.timestamp = detail::now_raw();
    ev.name_id = name_id;
    ev.thread_id = tls_handle.thread_id;
    ev.type = static_cast<std::uint8_t>(type);
    ev._pad = 0;

    if (!slot->ring->push(ev)) {
        // 링이 가득 찼다. 버린다. 블로킹하지 않는다.
        s.dropped.fetch_add(1, std::memory_order_relaxed);
    }
}

SessionStats Profiler::stats() {
    SessionState& s = session();
    SessionStats out;
    out.events_written = s.events_written.load();
    out.events_dropped = s.dropped.load();
    out.thread_count = s.next_thread_id.load();
    out.ticks_per_ns = s.ticks_per_ns;
    {
        std::lock_guard<std::mutex> lock(s.mutex);
        out.string_count = static_cast<std::uint32_t>(s.strings.size());
    }
    return out;
}

}  // namespace ft
