import type { ReactNode } from "react";
import { IconApply, IconFilter, IconGear, IconPlay, IconSliders, IconTable } from "./Icons";

interface Item {
  key: string;
  label: string;
  icon: ReactNode;
}

const MAIN: Item[] = [
  { key: "datasets", label: "데이터셋", icon: <IconTable /> },
  { key: "preprocess", label: "전처리", icon: <IconFilter /> },
  { key: "train", label: "학습 설정", icon: <IconSliders /> },
  { key: "runs", label: "실행·결과", icon: <IconPlay /> },
  { key: "apply", label: "새 데이터 적용", icon: <IconApply /> },
];
const SETTINGS: Item = { key: "settings", label: "설정", icon: <IconGear /> };

interface NavProps {
  current: string;
  /** 연결된 메뉴의 주소. null이면 아직 잠김(lockedHint 표시), 키가 없으면 연결 예정 */
  links: Record<string, string | null>;
  lockedHint: Record<string, string>;
}

function NavEntry({ item, props }: { item: Item; props: NavProps }) {
  const isCurrent = item.key === props.current;
  const href = props.links[item.key];
  const ready = typeof href === "string";
  const hint = ready ? null : item.key in props.links ? props.lockedHint[item.key] ?? "사용할 수 없음" : "연결 예정";
  const hintId = `nav-hint-${item.key}`;
  return (
    <li>
      <a
        className="nav-item"
        href={ready ? href : undefined}
        role={ready ? undefined : "link"}
        aria-current={isCurrent ? "page" : undefined}
        aria-disabled={ready ? undefined : true}
        aria-describedby={hint ? hintId : undefined}
        tabIndex={0}
        title={hint ? `${item.label} (${hint})` : item.label}
        onClick={(e) => !ready && e.preventDefault()}
      >
        <span className="nav-icon">{item.icon}</span>
        <span className="nav-text">
          <span className="nav-label">{item.label}</span>
          {hint && (
            <span className="nav-soon" id={hintId}>
              {hint}
            </span>
          )}
        </span>
      </a>
    </li>
  );
}

export function SideNav(props: NavProps) {
  return (
    <nav className="sidenav" aria-label="주 메뉴">
      <ul className="nav-list">
        {MAIN.map((it) => (
          <NavEntry key={it.key} item={it} props={props} />
        ))}
      </ul>
      <ul className="nav-list nav-bottom">
        <NavEntry item={SETTINGS} props={props} />
      </ul>
    </nav>
  );
}
