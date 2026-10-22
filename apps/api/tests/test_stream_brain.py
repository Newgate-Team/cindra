from stream_agent.brain import ChatBrain
from stream_agent.twitch_chat import ChatEvent


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _msg(user: str, text: str, mid: str = "", **kw) -> ChatEvent:
    return ChatEvent(kind="message", channel="c", user=user, display_name=user, text=text,
                     message_id=mid or f"{user}:{text}", **kw)


def _brain(**kw) -> tuple[ChatBrain, _Clock]:
    clock = _Clock()
    return ChatBrain(clock=clock, **kw), clock


def test_priorities_raid_then_bits_then_first_timer_then_question() -> None:
    brain, _ = _brain()
    brain.spoke()
    brain.observe(_msg("plain", "всем привет ребята"))
    brain.observe(_msg("asker", "а ты сама кто такая?"))
    brain.observe(_msg("newbie", "я тут впервые всем хай", first_message=True))
    brain.observe(_msg("cheer", "держи cheer на удачу", bits=500))
    brain.observe(ChatEvent(kind="notice", channel="c", user="raider", notice_type="raid",
                            viewer_count=120))
    order = []
    while (pick := brain.next_pick()) and pick.mode != "idle":
        order.append((pick.mode, pick.event.user))
    assert order == [
        ("notice", "raider"), ("answer", "cheer"), ("answer", "newbie"),
        ("answer", "asker"), ("answer", "plain"),
    ]


def test_noise_is_context_but_never_answered() -> None:
    brain, _ = _brain(bot_names=frozenset({"nightbot"}))
    brain.spoke()
    for user, text in [
        ("a", "!discord"), ("b", "lol"), ("c", "заходи на https://spam.example"),
        ("d", "😂😂😂"), ("nightbot", "Подписывайтесь на канал!"), ("e", "go to scam.xyz now"),
    ]:
        brain.observe(_msg(user, text))
    assert brain.next_pick() is None
    assert {e.user for e in brain.recent_chat()} == {"b", "c", "d", "e"}


def test_one_pending_message_per_viewer_and_the_latest_wins() -> None:
    brain, _ = _brain()
    brain.spoke()
    brain.observe(_msg("ann", "первое сообщение тут"))
    brain.observe(_msg("ann", "а вот второе, поновее"))
    assert brain.pending == 1
    assert brain.next_pick().event.text == "а вот второе, поновее"


def test_same_viewer_waits_their_turn() -> None:
    brain, clock = _brain(user_cooldown=120)
    brain.spoke()
    brain.observe(_msg("ann", "вопрос номер один?"))
    assert brain.next_pick().event.user == "ann"
    brain.observe(_msg("ann", "вопрос номер два?"))
    brain.observe(_msg("bob", "а я просто здороваюсь"))
    assert brain.next_pick().event.user == "bob"
    assert brain.next_pick() is None  # ann still cooling down, chat not idle yet
    clock.now += 121
    brain.observe(_msg("ann", "вопрос номер три?"))
    assert brain.next_pick().event.user == "ann"


def test_moderation_removes_messages_before_they_are_answered() -> None:
    brain, _ = _brain()
    brain.spoke()
    brain.observe(_msg("troll", "очень плохое сообщение", mid="m1"))
    brain.observe(_msg("spammer", "реклама реклама реклама", mid="m2"))
    brain.observe(_msg("ok", "нормальный вопрос?", mid="m3"))
    brain.observe(ChatEvent(kind="delete", channel="c", user="troll", message_id="m1"))
    brain.observe(ChatEvent(kind="clear_user", channel="c", user="spammer"))
    assert brain.next_pick().event.user == "ok"
    assert brain.next_pick() is None
    assert {e.user for e in brain.recent_chat()} == {"ok"}


def test_banned_raider_is_not_thanked_and_clear_chat_empties_everything() -> None:
    brain, _ = _brain()
    brain.spoke()
    brain.observe(ChatEvent(kind="notice", channel="c", user="bad", notice_type="raid"))
    brain.observe(ChatEvent(kind="clear_user", channel="c", user="bad"))
    brain.observe(_msg("x", "какое-то сообщение"))
    brain.observe(ChatEvent(kind="clear_chat", channel="c"))
    assert brain.next_pick() is None
    assert brain.recent_chat() == []


def test_stale_messages_expire_and_quiet_chat_triggers_idle_talk() -> None:
    brain, clock = _brain(max_age=90, idle_after=45)
    brain.spoke()
    brain.observe(_msg("late", "это было давно?"))
    clock.now += 91
    pick = brain.next_pick()
    assert pick.mode == "idle"  # the old question is gone, and it's been quiet
    brain.spoke()
    clock.now += 10
    assert brain.next_pick() is None
    clock.now += 40
    assert brain.next_pick().mode == "idle"


def test_stream_opens_with_idle_talk_and_mentions_rank_up() -> None:
    brain, _ = _brain(name_words=("синди",))
    assert brain.next_pick().mode == "idle"  # nothing said yet: open the show
    brain.spoke()
    brain.observe(_msg("a", "кто-нибудь смотрел матч вчера"))
    brain.observe(_msg("b", "синди, привет от меня"))
    assert brain.next_pick().event.user == "b"


def test_copypasta_wave_counts_once() -> None:
    brain, _ = _brain()
    brain.spoke()
    for user in ("a", "b", "c", "d"):
        brain.observe(_msg(user, "ВСЕ ПИШЕМ ПЛЮС В ЧАТ"))
    assert brain.pending == 1
