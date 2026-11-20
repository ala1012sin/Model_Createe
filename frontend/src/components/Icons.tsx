// 단순 선 아이콘(장식용, 이름은 옆 텍스트·aria-label이 전달)
const base = { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };

export const IconTable = () => (
  <svg {...base}><rect x="3.5" y="4.5" width="17" height="15" rx="1.5" /><path d="M3.5 9.5h17M9.5 9.5v10" /></svg>
);
export const IconFilter = () => (
  <svg {...base}><path d="M4 5h16l-6 7.5V19l-4-2v-4.5z" /></svg>
);
export const IconSliders = () => (
  <svg {...base}><path d="M5 4v16M12 4v16M19 4v16" /><circle cx="5" cy="9" r="2" fill="currentColor" stroke="none" />
    <circle cx="12" cy="15" r="2" fill="currentColor" stroke="none" /><circle cx="19" cy="7" r="2" fill="currentColor" stroke="none" /></svg>
);
export const IconPlay = () => (
  <svg {...base}><path d="M4 19h16" /><path d="M7 15l3.5-4 3 2.5L18 7" /></svg>
);
export const IconApply = () => (
  <svg {...base}><path d="M12 3.5v11M7.5 10l4.5 4.5 4.5-4.5" /><path d="M4.5 16.5v3h15v-3" /></svg>
);
export const IconGear = () => (
  <svg {...base}><circle cx="12" cy="12" r="3" />
    <path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8" /></svg>
);
export const IconUpload = () => (
  <svg {...base} width={22} height={22}><path d="M12 15.5V4.5M7.5 9L12 4.5 16.5 9" /><path d="M4.5 15v4.5h15V15" /></svg>
);
