import { useEffect, useState } from "react";

// 앱 공통 알림(토스트). 화면을 넘어가도 잠깐 남는다(ADD-UX-09).
const EVENT = "mlwb-toast";

export function toast(text: string, kind: "ok" | "warn" = "ok") {
  window.dispatchEvent(new CustomEvent(EVENT, { detail: { text, kind } }));
}

export function Toaster() {
  const [items, setItems] = useState<{ id: number; text: string; kind: string }[]>([]);
  useEffect(() => {
    let n = 0;
    const on = (e: Event) => {
      const { text, kind } = (e as CustomEvent).detail as { text: string; kind: string };
      const id = ++n;
      setItems((cur) => [...cur.slice(-2), { id, text, kind }]);
      window.setTimeout(() => setItems((cur) => cur.filter((x) => x.id !== id)), 3200);
    };
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

/** 단계 화면 제목 위 '← 이전 단계' 링크(ADD-UX-10). */
export function BackLink({ href, label }: { href: string; label: string }) {
  return (
    <a className="back-link" href={href}>
      ← {label}
    </a>
  );
}
