import {
  ArrowRight,
  CalendarDays,
  ChartNoAxesCombined,
  Check,
  Clapperboard,
  LayoutTemplate,
  Radio,
  Sparkles,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import type { CSSProperties } from "react";

import { TIER_FEATURES, TIER_NAME, TIER_ORDER, TIER_PRICE } from "@/lib/plans";

import { Poster, SectionTile } from "./Brand";

const PLATFORMS: { name: string; color: string }[] = [
  { name: "Telegram", color: "#229ed9" },
  { name: "Instagram", color: "#e1306c" },
  { name: "Facebook", color: "#1877f2" },
  { name: "TikTok", color: "#111111" },
  { name: "YouTube", color: "#ff0000" },
  { name: "LinkedIn", color: "#0a66c2" },
  { name: "Reddit", color: "#ff4500" },
  { name: "X", color: "#111111" },
];

const FEATURES: { title: string; text: string; icon: LucideIcon; color: string }[] = [
  {
    title: "Посты",
    text: "Тема → текст, картинка или сценарий → правка → публикация сразу или по расписанию, в несколько аккаунтов одновременно.",
    icon: Sparkles,
    color: "#7c5cff",
  },
  {
    title: "Видео и аватар",
    text: "Сценарий, стиль и производственный бриф. Готовый ролик — свой или сгенерированный; с вашим лицом и голосом через ваш аккаунт HeyGen.",
    icon: UserRound,
    color: "#ed4c88",
  },
  {
    title: "Монтаж",
    text: "Клипы, музыка и субтитры собираются в ролик в DaVinci. Или скачайте монтажный лист и доведите монтаж у себя.",
    icon: Clapperboard,
    color: "#8b5cf6",
  },
  {
    title: "Эфир 24/7",
    text: "AI-аватар ведёт трансляцию на Twitch: читает чат, отвечает голосом, благодарит за подписки и рейды, а в паузах говорит на ваши темы.",
    icon: Radio,
    color: "#e95353",
  },
  {
    title: "Шаблоны и A/B",
    text: "Карточки, где текст встаёт ровно как введён. И несколько вариантов поста, из которых вы выбираете победителя.",
    icon: LayoutTemplate,
    color: "#14a995",
  },
  {
    title: "Календарь и аналитика",
    text: "План публикаций, перенос перетаскиванием и понятные цифры: что вышло, куда и сколько лимита осталось.",
    icon: ChartNoAxesCombined,
    color: "#23a466",
  },
];

const STEPS = ["Идея", "Генерация", "Правка", "Публикация", "Цифры"];

const FAQ: { q: string; a: string }[] = [
  {
    q: "Кто оплачивает HeyGen и эфир?",
    a: "Вы — по своему аккаунту у сервиса аватара (HeyGen, LiveAvatar или Simli). Cindra отдельно показывает расход по тарифу и ориентир стоимости у поставщика, поэтому дорогие вещи не прячутся в подписке.",
  },
  {
    q: "Где хранятся ключи и доступы?",
    a: "В зашифрованном виде. После сохранения ключ в интерфейсе не показывается — только отметка «сохранён». Любой доступ можно отключить в разделе «Инструменты».",
  },
  {
    q: "В какие соцсети можно публиковать?",
    a: "Telegram, Instagram, Facebook, TikTok, YouTube, LinkedIn, Reddit и X. Не везде подходит любой формат: например, TikTok и YouTube принимают только видео, X — только текст. Cindra сама подскажет, что куда можно отправить.",
  },
  {
    q: "Можно ли работать командой?",
    a: "Да. Владелец создаёт команду и приглашает коллег: соцсети, публикации и генерации становятся общими, а тариф меняет только владелец.",
  },
];

export function Landing() {
  return (
    <div className="landing">
      <section className="hero hero-center">
        <div className="eyebrow">Ваша идея — наша искра</div>
        <h1 className="hero-headline">
          Весь контент.
          <br />
          Одна студия.
          <br />
          Ваша история.
        </h1>
        <p className="muted hero-lead">
          Придумывайте, создавайте и публикуйте контент для соцсетей. От первого слова до круглосуточного эфира.
        </p>
        <div className="cta-row">
          <Link href="/register" className="button-link primary big">
            Начать бесплатно <ArrowRight size={18} />
          </Link>
          <Link href="/login" className="button-link secondary big">
            Войти
          </Link>
        </div>
      </section>

      <div className="platform-row" aria-label="Площадки публикации">
        {PLATFORMS.map((p) => (
          <span key={p.name} className="tag">
            <i className="dot" style={{ background: p.color }} aria-hidden="true" />
            {p.name}
          </span>
        ))}
      </div>

      <div className="grid three section posters">
        <Poster title="Идеи становятся постами" tone="violet" />
        <Poster title="Истории оживают" tone="sun" />
        <Poster title="Контент по плану" tone="leaf" />
      </div>

      <section className="section">
        <h2 className="landing-h2">Всё, что нужно контенту, — в одном окне</h2>
        <div className="grid three bento">
          {FEATURES.map((feature) => (
            <article key={feature.title} className="panel feature" style={{ "--t": feature.color } as CSSProperties}>
              <SectionTile section={feature} size={20} />
              <h3>{feature.title}</h3>
              <p className="muted">{feature.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="panel section">
        <h2>От темы до публикации</h2>
        <ol className="flow">
          {STEPS.map((step, index) => (
            <li key={step}>
              <span className="flow-num">{String(index + 1).padStart(2, "0")}</span>
              {step}
            </li>
          ))}
        </ol>
        <p className="muted">
          <CalendarDays size={15} className="inline-icon" aria-hidden="true" /> Каждый шаг можно остановить и поправить: пост уходит в соцсети, только когда вы нажали «Опубликовать» или назначили время.
        </p>
      </section>

      <section className="section">
        <h2 className="landing-h2">Тарифы</h2>
        <div className="grid three plans">
          {TIER_ORDER.map((tier) => (
            <article key={tier} className={`panel plan${tier === "pro" ? " featured" : ""}`}>
              {tier === "pro" && <span className="badge plan">ПОПУЛЯРНЫЙ</span>}
              <h3>{TIER_NAME[tier]}</h3>
              <p className="price">
                {TIER_PRICE[tier]}
                <span> / мес</span>
              </p>
              <ul className="check-list">
                {TIER_FEATURES[tier].map((feature) => (
                  <li key={feature}>
                    <Check size={16} strokeWidth={3} aria-hidden="true" />
                    {feature}
                  </li>
                ))}
              </ul>
              <Link href="/register" className={`button-link ${tier === "pro" ? "primary" : "secondary"}`}>
                {tier === "free" ? "Начать бесплатно" : "Попробовать"}
              </Link>
            </article>
          ))}
        </div>
        <p className="muted plans-note">Аватар-видео и эфир оплачиваются с вашего аккаунта у сервиса аватара; тариф Cindra задаёт лишь количество в месяц.</p>
      </section>

      <section className="panel section">
        <h2>Частые вопросы</h2>
        <div className="faq">
          {FAQ.map((item) => (
            <details key={item.q}>
              <summary>{item.q}</summary>
              <p className="muted">{item.a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="cta-block section">
        <h2>Ваша следующая большая идея — уже здесь</h2>
        <p>Бесплатный тариф без карты: попробуйте путь от темы до публикации сами.</p>
        <Link href="/register" className="button-link secondary big">
          Создать аккаунт <ArrowRight size={18} />
        </Link>
      </section>
    </div>
  );
}
