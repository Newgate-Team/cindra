import { Compass } from "lucide-react";
import Link from "next/link";

export const metadata = { title: "Страница не найдена" };

export default function NotFound() {
  return (
    <section className="panel empty">
      <span className="tile" aria-hidden="true">
        <Compass size={26} />
      </span>
      <h1>Такой страницы нет</h1>
      <p className="muted">Возможно, ссылка устарела или в адресе опечатка. Вернёмся к вашим идеям?</p>
      <Link className="button-link" href="/">
        На главную
      </Link>
    </section>
  );
}
