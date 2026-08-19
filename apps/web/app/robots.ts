import type { MetadataRoute } from "next";

// app.cindra.online — приватный кабинет (CRW-13): полностью закрыт от
// индексации. Пара к robots: {index: false} в app/layout.tsx — meta-тег
// деиндексирует уже известные краулеру страницы, а robots.txt
// останавливает обход новых. До этого файла /robots.txt отдавал 404.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", disallow: "/" },
  };
}
