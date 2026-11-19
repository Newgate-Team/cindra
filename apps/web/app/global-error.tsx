"use client";

// The last line of defence: an error in the root layout itself, where
// the shell and the stylesheet may not exist -- so this page carries its
// own minimal styling.
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="ru">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          background: "#f6f7fb",
          color: "#14161f",
          fontFamily: "system-ui, sans-serif",
          textAlign: "center",
          padding: 24,
        }}
      >
        <div>
          <h1 style={{ marginBottom: 8 }}>Cindra временно недоступна</h1>
          <p style={{ color: "#626879", marginBottom: 20 }}>Мы уже знаем о проблеме. Попробуйте обновить страницу.</p>
          <button
            type="button"
            onClick={reset}
            style={{
              padding: "11px 20px",
              border: 0,
              borderRadius: 11,
              background: "linear-gradient(135deg, #e46d32, #df355b)",
              color: "#fff",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Повторить
          </button>
        </div>
      </body>
    </html>
  );
}
