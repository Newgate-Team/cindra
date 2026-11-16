import {
  Blocks,
  CalendarDays,
  ChartNoAxesCombined,
  Clapperboard,
  FlaskConical,
  GalleryHorizontalEnd,
  Gem,
  LayoutDashboard,
  LayoutTemplate,
  Radio,
  Settings,
  Share2,
  Sparkles,
  Users,
  type LucideIcon,
} from "lucide-react";

export interface NavSection {
  href: string;
  label: string;
  icon: LucideIcon;
  // The section's own colour: the icon tile in the menu, the active
  // highlight and the accent of its page. One colour per section is how
  // people learn where they are (the redesign's core rule).
  color: string;
}

export interface NavGroup {
  // Empty for the first block (Обзор stands alone above the groups).
  title: string;
  sections: NavSection[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    title: "",
    sections: [{ href: "/", label: "Обзор", icon: LayoutDashboard, color: "#ee7c52" }],
  },
  {
    title: "Создавать",
    sections: [
      { href: "/generate", label: "Посты", icon: Sparkles, color: "#7c5cff" },
      { href: "/video", label: "Видео", icon: Clapperboard, color: "#ed4c88" },
      { href: "/streams", label: "Эфир", icon: Radio, color: "#e95353" },
      { href: "/templates", label: "Шаблоны", icon: LayoutTemplate, color: "#14a995" },
      { href: "/ab-tests", label: "A/B-тесты", icon: FlaskConical, color: "#d99b22" },
    ],
  },
  {
    title: "Планировать и смотреть",
    sections: [
      { href: "/calendar", label: "Календарь", icon: CalendarDays, color: "#3b82f6" },
      { href: "/feed", label: "Лента", icon: GalleryHorizontalEnd, color: "#0ea5e9" },
      { href: "/analytics", label: "Аналитика", icon: ChartNoAxesCombined, color: "#23a466" },
    ],
  },
  {
    title: "Подключения",
    sections: [
      { href: "/social-accounts", label: "Соцсети", icon: Share2, color: "#5b6cff" },
      { href: "/integrations", label: "Инструменты", icon: Blocks, color: "#e88a3d" },
    ],
  },
  {
    title: "Аккаунт",
    sections: [
      { href: "/team", label: "Команда", icon: Users, color: "#06a6c4" },
      { href: "/billing", label: "Тариф", icon: Gem, color: "#d5a213" },
      { href: "/settings", label: "Настройки", icon: Settings, color: "#64748b" },
    ],
  },
];

export const NAV_SECTIONS: NavSection[] = NAV_GROUPS.flatMap((group) => group.sections);

// On a phone the bar holds five slots; everything else lives behind
// "Ещё" (see MobileMore in NavBar).
export const BOTTOM_TABS: string[] = ["/", "/generate", "/video", "/calendar"];

// Longest matching prefix wins, so /video/montage highlights «Видео»
// and "/" only matches the home page itself.
export function sectionFor(pathname: string): NavSection | undefined {
  let best: NavSection | undefined;
  for (const section of NAV_SECTIONS) {
    const matches =
      section.href === "/"
        ? pathname === "/"
        : pathname === section.href || pathname.startsWith(`${section.href}/`);
    if (matches && (!best || section.href.length > best.href.length)) best = section;
  }
  return best;
}

export const CREATE_ACTIONS: { href: string; label: string; hint: string; icon: LucideIcon; color: string }[] = [
  { href: "/generate", label: "Пост", hint: "Текст, картинка или сценарий", icon: Sparkles, color: "#7c5cff" },
  { href: "/video", label: "Видео-проект", hint: "От сценария до ролика", icon: Clapperboard, color: "#ed4c88" },
  { href: "/video/avatar", label: "Аватар-видео", hint: "С вашим лицом и голосом", icon: Sparkles, color: "#ed4c88" },
  { href: "/video/montage", label: "Монтаж", hint: "Клипы, музыка, субтитры", icon: Clapperboard, color: "#ed4c88" },
  { href: "/streams", label: "Эфир", hint: "Аватар ведёт трансляцию", icon: Radio, color: "#e95353" },
];
