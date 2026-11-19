"use client";

import { CloudOff } from "lucide-react";
import { useEffect } from "react";

// Anything a page throws while rendering lands here instead of a blank
// screen. The shell (menu, top bar) stays; only the page area is replaced.
export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <section className="panel empty" role="alert">
      <span className="tile" aria-hidden="true">
        <CloudOff size={26} />
      </span>
      <h1>Не удалось показать страницу</h1>
      <p className="muted">
        Что-то пошло не так на нашей стороне. Попробуйте ещё раз — если повторится, обновите страницу или загляните
        позже.
      </p>
      {error.digest && <small className="muted">Код ошибки: {error.digest}</small>}
      <button type="button" onClick={reset}>
        Повторить
      </button>
    </section>
  );
}
