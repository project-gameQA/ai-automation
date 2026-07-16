// frametrace - trace_format.h
// .ftrace 바이너리 포맷 정의.
//
// 파일 레이아웃:
//   [FileHeader]
//   [Event][Event][Event]...          <- event_count개
//   [StringTable]                      <- 파일 끝. 오프셋은 헤더에 기록.
//
// 설계 근거:
//   - 이벤트를 먼저, 문자열 테이블을 나중에 쓴다. 실행 중에는 어떤 문자열이
//     쓰일지 미리 알 수 없기 때문이다. 스트리밍으로 이벤트를 계속 뱉다가
//     종료 시점에 테이블을 덧붙이고 헤더를 다시 쓴다.
//   - Event는 정확히 16바이트다. 캐시 라인 하나에 4개가 들어간다.
//     구조체 패딩으로 크기가 늘어나면 링 버퍼 처리량이 그만큼 떨어진다.
//     static_assert로 강제한다.
//   - 문자열은 인터닝한다. 이벤트마다 이름 문자열을 넣으면 트레이스가
//     수백 MB가 된다. 32비트 ID로 참조하면 4바이트다.
//   - 텍스트(JSON)가 아니라 바이너리인 이유: 초당 수백만 이벤트가 나온다.
//     JSON 직렬화는 그 자체로 프로파일링 대상보다 비싸다.
//   - 버전 필드를 둔다. 포맷이 바뀌면 뷰어가 명확히 거부할 수 있어야 한다.

#pragma once

#include <cstdint>

namespace ft {

// "FTRC" 리틀엔디안
constexpr std::uint32_t kTraceMagic = 0x43525446u;
constexpr std::uint16_t kTraceVersion = 1;

enum class EventType : std::uint8_t {
    ScopeBegin = 0,
    ScopeEnd = 1,
    FrameMark = 2,  // 프레임 경계
    Instant = 3,    // 순간 이벤트, 지속 시간 없음
};

#pragma pack(push, 1)

struct FileHeader {
    std::uint32_t magic;
    std::uint16_t version;
    std::uint16_t header_size;

    // TSC 틱 -> 나노초 변환 계수. 뷰어가 이걸로 실제 시간을 복원한다.
    double ticks_per_ns;

    // 이 트레이스의 시간 원점. 모든 이벤트 타임스탬프는 절대값이지만
    // 뷰어에서는 이 값을 빼서 0부터 시작하게 표시한다.
    std::uint64_t base_timestamp;

    std::uint64_t event_count;
    std::uint64_t string_table_offset;
    std::uint32_t string_count;

    // 링 버퍼가 가득 차서 버려진 이벤트 수. 0이 아니면 트레이스에
    // 구멍이 있다는 뜻이므로 뷰어가 경고를 띄워야 한다.
    std::uint64_t dropped_events;

    std::uint32_t thread_count;
    std::uint32_t _reserved;
};

// 정확히 16바이트여야 한다.
struct Event {
    std::uint64_t timestamp;  // 원시 틱
    std::uint32_t name_id;    // 문자열 테이블 인덱스
    std::uint16_t thread_id;  // 내부 스레드 인덱스 (OS TID 아님)
    std::uint8_t type;        // EventType
    std::uint8_t _pad;
};

#pragma pack(pop)

static_assert(sizeof(Event) == 16, "Event must be exactly 16 bytes");

// 문자열 테이블 형식 (string_table_offset부터):
//   각 항목마다: [uint16 length][length바이트 문자열, 널 종료 없음]
//   string_count개만큼 반복.
//
// 스레드 이름도 같은 테이블에 들어간다. 파일 끝에 스레드 이름
// ID 배열이 thread_count개 붙는다.

}  // namespace ft
