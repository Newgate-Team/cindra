import Link from "next/link";

export function Footer() {
  return (
    <footer>
      <Link href="/privacy">Политика конфиденциальности</Link>
      <Link href="/terms">Условия использования</Link>
      <Link href="/data-deletion">Удаление данных</Link>
    </footer>
  );
}
