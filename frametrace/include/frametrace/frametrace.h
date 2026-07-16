// frametrace - frametrace.h
// 공개 API.
//
// 사용법:
//   ft::Profiler::begin_session("out.ftrace");
//   ...
//   void update() {
//       FT_SCOPE("update");          // 스코프 시작~끝을 자동으로 잰다
//       FT_SCOPE("physics");
//   }
//   FT_FRAME_MARK();                 // 프레임 경계 표시
//   ...
//   ft::Profiler::end_session();
//
// FT_ENABLED=0으로 빌드하면 모든 매크로가 완전히 사라진다.
// 릴리스 빌드에서 오버헤드가 0이 되어야 하기 때문이다. 런타임 if로
// 끄면 분기 예측 비용과 코드 크기가 남는다.

#pragma once

#include <cstdint>

#include "frametrace/detail/clock.h"
#include "frametrace/trace_format.h"

#ifndef FT_ENABLED
#define FT_ENABLED 1
#endif

namespace ft {

struct SessionStats {
    std::uint64_t events_written = 0;
    std::uint64_t events_dropped = 0;
    std::uint32_t thread_count = 0;
    std::uint32_t string_count = 0;
    double ticks_per_ns = 1.0;
};

class Profiler {
public:
    // 세션을 시작한다. 캘리브레이션(약 10ms)과 플러시 스레드 생성이 일어난다.
    // 이미 세션이 열려 있으면 false.
    static bool begin_session(const char* output_path);

    // 남은 이벤트를 모두 플러시하고 문자열 테이블을 쓰고 파일을 닫는다.
    static SessionStats end_session();

    static bool is_active();

    // 현재 스레드에 이름을 붙인다. 뷰어에서 트랙 레이블로 쓰인다.
    // 스레드마다 한 번 호출한다.
    static void name_current_thread(const char* name);

    // 문자열을 등록하고 ID를 받는다.
    // 같은 포인터로 다시 부르면 같은 ID가 나온다. 매크로가 static 지역
    // 변수로 캐싱하므로 실제로는 스코프당 한 번만 호출된다.
    static std::uint32_t intern(const char* str);

    // 이벤트 하나를 기록한다. 핫 패스.
    static void emit(EventType type, std::uint32_t name_id) noexcept;

    static SessionStats stats();
};

namespace detail {

// RAII 타이머. 생성 시 Begin, 소멸 시 End를 기록한다.
// 예외가 나가도 소멸자는 불리므로 스코프가 짝이 안 맞을 일이 없다.
class ScopeTimer {
public:
    explicit ScopeTimer(std::uint32_t name_id) noexcept : name_id_(name_id) {
        Profiler::emit(EventType::ScopeBegin, name_id_);
    }

    ~ScopeTimer() noexcept { Profiler::emit(EventType::ScopeEnd, name_id_); }

    ScopeTimer(const ScopeTimer&) = delete;
    ScopeTimer& operator=(const ScopeTimer&) = delete;

private:
    std::uint32_t name_id_;
};

}  // namespace detail
}  // namespace ft

// ---------------------------------------------------------------------------
// 매크로
// ---------------------------------------------------------------------------

#define FT_CONCAT_INNER(a, b) a##b
#define FT_CONCAT(a, b) FT_CONCAT_INNER(a, b)
#define FT_UNIQUE(name) FT_CONCAT(name, __LINE__)

#if FT_ENABLED

// static 지역 변수를 쓰는 이유:
//   intern()은 해시맵 조회다. 매 호출마다 하면 비싸다. static 지역은
//   C++11부터 스레드 안전하게 딱 한 번만 초기화된다. 이후 호출은
//   가드 변수 원자적 로드 한 번(약 1ns)으로 끝난다.
#define FT_SCOPE(name)                                                 \
    static const std::uint32_t FT_UNIQUE(ft_id_) = ::ft::Profiler::intern(name); \
    ::ft::detail::ScopeTimer FT_UNIQUE(ft_scope_)(FT_UNIQUE(ft_id_))

// 함수 이름을 자동으로 쓴다.
#if defined(_MSC_VER)
#define FT_FUNC() FT_SCOPE(__FUNCTION__)
#else
#define FT_FUNC() FT_SCOPE(__func__)
#endif

#define FT_FRAME_MARK()                                                  \
    do {                                                                 \
        static const std::uint32_t FT_UNIQUE(ft_fid_) =                  \
            ::ft::Profiler::intern("frame");                             \
        ::ft::Profiler::emit(::ft::EventType::FrameMark,                 \
                             FT_UNIQUE(ft_fid_));                        \
    } while (0)

#define FT_INSTANT(name)                                                 \
    do {                                                                 \
        static const std::uint32_t FT_UNIQUE(ft_iid_) =                  \
            ::ft::Profiler::intern(name);                                \
        ::ft::Profiler::emit(::ft::EventType::Instant, FT_UNIQUE(ft_iid_)); \
    } while (0)

#define FT_THREAD_NAME(name) ::ft::Profiler::name_current_thread(name)

#else  // FT_ENABLED == 0

// 완전히 사라진다. 컴파일러가 볼 것도 없다.
#define FT_SCOPE(name) ((void)0)
#define FT_FUNC() ((void)0)
#define FT_FRAME_MARK() ((void)0)
#define FT_INSTANT(name) ((void)0)
#define FT_THREAD_NAME(name) ((void)0)

#endif  // FT_ENABLED
