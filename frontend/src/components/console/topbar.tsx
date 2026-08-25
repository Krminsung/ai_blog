"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/badge";
import { ThemeToggle } from "@/components/ui/theme";
import { notifications as notificationsApi } from "@/lib/api/endpoints";
import { formatRelative } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";
import { useSession } from "@/lib/auth/session-provider";

export function Topbar({ onOpenNav }: { onOpenNav: () => void }) {
  const { user, workspace, workspaces, switchWorkspace, signOut } = useSession();
  const [menu, setMenu] = useState<"none" | "workspace" | "account" | "bell">(
    "none",
  );
  const rootRef = useRef<HTMLDivElement>(null);

  // Unread badge; polls gently because notifications are not pushed.
  const { data: notifications } = useApi(
    "notifications-unread",
    () => notificationsApi.list({ limit: 10 }),
    { refreshInterval: 60_000 },
  );
  const unread = (notifications ?? []).filter((item) => !item.read_at);

  useEffect(() => {
    if (menu === "none") return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setMenu("none");
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu("none");
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menu]);

  const initials = (user?.display_name ?? "?").trim().slice(0, 1).toUpperCase();

  return (
    <div
      ref={rootRef}
      className="chrome-blur hairline-b sticky top-0 z-40 flex h-14 items-center gap-3 px-4 sm:px-6"
    >
      <button
        type="button"
        onClick={onOpenNav}
        aria-label="메뉴 열기"
        className="grid size-9 shrink-0 place-items-center rounded-[9px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-alt)] lg:hidden"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
          <path
            d="M2 4h12M2 8h12M2 12h12"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </button>

      {/* Workspace switcher */}
      <div className="relative min-w-0">
        <button
          type="button"
          onClick={() => setMenu(menu === "workspace" ? "none" : "workspace")}
          aria-expanded={menu === "workspace"}
          aria-haspopup="menu"
          className="flex min-w-0 items-center gap-2 rounded-[10px] px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--surface-alt)]"
        >
          <span className="grid size-6 shrink-0 place-items-center rounded-[7px] bg-[var(--accent)] text-[11px] font-semibold text-white">
            {(workspace?.name ?? "W").slice(0, 1).toUpperCase()}
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[13.5px] font-medium">
              {workspace?.name ?? "워크스페이스"}
            </span>
          </span>
          <svg
            width="10"
            height="10"
            viewBox="0 0 10 10"
            fill="none"
            aria-hidden
            className="shrink-0 text-[var(--text-tertiary)]"
          >
            <path
              d="M2 4l3 3 3-3"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>

        {menu === "workspace" ? (
          <Dropdown className="left-0 w-64">
            <p className="px-3 pt-2 pb-1 text-[11px] font-semibold tracking-[0.05em] text-[var(--text-tertiary)] uppercase">
              워크스페이스
            </p>
            {workspaces.length === 0 ? (
              <p className="px-3 py-2 text-[13px] text-[var(--text-secondary)]">
                접근 가능한 워크스페이스가 없습니다.
              </p>
            ) : (
              workspaces.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setMenu("none");
                    void switchWorkspace(item.id);
                  }}
                  className={cn(
                    "flex w-full items-center justify-between gap-2 rounded-[8px] px-3 py-2 text-left text-[13.5px] transition-colors hover:bg-[var(--surface-alt)]",
                    item.id === workspace?.id && "font-medium",
                  )}
                >
                  <span className="min-w-0 truncate">{item.name}</span>
                  {item.id === workspace?.id ? (
                    <span aria-hidden className="text-[var(--accent)]">
                      ✓
                    </span>
                  ) : null}
                </button>
              ))
            )}
            <div className="my-1 h-px bg-[var(--hairline-soft)]" />
            <Link
              href="/console/settings"
              onClick={() => setMenu("none")}
              className="block rounded-[8px] px-3 py-2 text-[13.5px] transition-colors hover:bg-[var(--surface-alt)]"
            >
              워크스페이스 설정
            </Link>
          </Dropdown>
        ) : null}
      </div>

      <div className="flex-1" />

      <ThemeToggle className="hidden sm:inline-flex" />

      {/* Notifications */}
      <div className="relative">
        <button
          type="button"
          onClick={() => setMenu(menu === "bell" ? "none" : "bell")}
          aria-expanded={menu === "bell"}
          aria-label={`알림 ${unread.length}건`}
          className="relative grid size-9 place-items-center rounded-[9px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-alt)]"
        >
          <svg width="17" height="17" viewBox="0 0 18 18" fill="none" aria-hidden>
            <path
              d="M9 2a5 5 0 0 0-5 5v3l-1.2 2.4A.5.5 0 0 0 3.25 14h11.5a.5.5 0 0 0 .45-.72L14 10.99V7a5 5 0 0 0-5-5Z"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinejoin="round"
            />
            <path
              d="M7 15a2 2 0 0 0 4 0"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
            />
          </svg>
          {unread.length > 0 ? (
            <span className="absolute top-1.5 right-1.5 size-2 rounded-full bg-[var(--critical)]" />
          ) : null}
        </button>

        {menu === "bell" ? (
          <Dropdown className="right-0 w-80">
            <p className="px-3 pt-2 pb-1 text-[11px] font-semibold tracking-[0.05em] text-[var(--text-tertiary)] uppercase">
              알림
            </p>
            {(notifications ?? []).length === 0 ? (
              <p className="px-3 py-4 text-center text-[13px] text-[var(--text-secondary)]">
                새 알림이 없습니다.
              </p>
            ) : (
              <ul className="max-h-80 overflow-y-auto">
                {(notifications ?? []).map((item) => (
                  <li
                    key={item.id}
                    className="rounded-[8px] px-3 py-2.5 hover:bg-[var(--surface-alt)]"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-[13.5px] font-medium">{item.title}</p>
                      {!item.read_at ? <Badge tone="progress">새 알림</Badge> : null}
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-[12.5px] text-[var(--text-secondary)]">
                      {item.safe_summary}
                    </p>
                    <p className="type-caption mt-1">
                      {formatRelative(item.created_at)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Dropdown>
        ) : null}
      </div>

      {/* Account */}
      <div className="relative">
        <button
          type="button"
          onClick={() => setMenu(menu === "account" ? "none" : "account")}
          aria-expanded={menu === "account"}
          aria-haspopup="menu"
          aria-label="계정 메뉴"
          className="grid size-8 place-items-center rounded-full bg-[var(--surface-alt)] text-[13px] font-semibold transition-colors hover:bg-[var(--hairline-soft)]"
        >
          {initials}
        </button>

        {menu === "account" ? (
          <Dropdown className="right-0 w-60">
            <div className="px-3 py-2">
              <p className="truncate text-[13.5px] font-medium">
                {user?.display_name ?? "—"}
              </p>
              <p className="truncate text-[12px] text-[var(--text-secondary)]">
                {user?.email ?? ""}
              </p>
            </div>
            <div className="my-1 h-px bg-[var(--hairline-soft)]" />
            <Link
              href="/console/settings/profile"
              onClick={() => setMenu("none")}
              className="block rounded-[8px] px-3 py-2 text-[13.5px] transition-colors hover:bg-[var(--surface-alt)]"
            >
              프로필과 보안
            </Link>
            <Link
              href="/"
              onClick={() => setMenu("none")}
              className="block rounded-[8px] px-3 py-2 text-[13.5px] transition-colors hover:bg-[var(--surface-alt)]"
            >
              웹사이트로 이동
            </Link>
            <div className="my-1 h-px bg-[var(--hairline-soft)]" />
            <button
              type="button"
              onClick={() => {
                setMenu("none");
                void signOut();
              }}
              className="block w-full rounded-[8px] px-3 py-2 text-left text-[13.5px] text-[var(--critical)] transition-colors hover:bg-[var(--critical-soft)]"
            >
              로그아웃
            </button>
          </Dropdown>
        ) : null}
      </div>
    </div>
  );
}

function Dropdown({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      role="menu"
      className={cn(
        "absolute top-[calc(100%+8px)] z-50 rounded-[14px] border border-[var(--hairline-soft)] p-1.5",
        "bg-[var(--surface-raised)] shadow-[var(--shadow-float)]",
        "motion-safe:animate-[dropdown-in_.18s_var(--ease-apple)]",
        className,
      )}
    >
      {children}
      <style>{`
        @keyframes dropdown-in {
          from { opacity: 0; transform: translateY(-4px) scale(.98); }
          to   { opacity: 1; transform: none; }
        }
      `}</style>
    </div>
  );
}
