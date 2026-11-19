// CIN-138: mirrors prompts.TONE_GUIDANCE keys on the backend. One list
// for every form that offers a tone (posts, A/B tests, brand kit).
export const TONE_OPTIONS = [
  { value: "", label: "По умолчанию" },
  { value: "expert", label: "Экспертный" },
  { value: "conversational", label: "Разговорный" },
  { value: "provocative", label: "Провокационный" },
  { value: "storytelling", label: "Сторителлинг" },
];
