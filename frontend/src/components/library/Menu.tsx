import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** One line of a menu. `separator` draws a line instead; `checked` makes it a choice among others. */
export interface MenuItem {
  label?: string;
  icon?: ReactNode;
  hint?: string; // a keyboard shortcut, shown on the right
  onSelect?: () => void;
  disabled?: boolean;
  danger?: boolean;
  checked?: boolean;
  separator?: boolean;
}

/**
 * The list itself: focus goes to the first thing that can be chosen, the arrow keys move, Home
 * and End jump, Enter or Space choose, and Esc, Tab or a click anywhere else close it.
 */
function MenuList({
  items,
  label,
  onClose,
  style,
  listRef,
  ignore,
}: {
  items: MenuItem[];
  label: string;
  onClose: (refocus: boolean) => void;
  style?: React.CSSProperties;
  listRef?: React.RefObject<HTMLDivElement>;
  /** Clicks inside this (the button that opened the menu) are its own business. */
  ignore?: React.RefObject<HTMLElement>;
}) {
  const own = useRef<HTMLDivElement>(null);
  const ref = listRef ?? own;

  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    const away = (e: Event) => {
      const target = e.target as Node;
      if (!ref.current?.contains(target) && !(e.type === "pointerdown" && ignore?.current?.contains(target))) onClose(false);
    };
    // Capture: a click that also opens something else still closes this first.
    document.addEventListener("pointerdown", away, true);
    window.addEventListener("resize", away);
    window.addEventListener("blur", away);
    return () => {
      document.removeEventListener("pointerdown", away, true);
      window.removeEventListener("resize", away);
      window.removeEventListener("blur", away);
    };
  }, [onClose, ref, ignore]);

  function onKeyDown(e: React.KeyboardEvent) {
    const buttons = [...(ref.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const go = (i: number) => buttons[(i + buttons.length) % buttons.length]?.focus();
    if (e.key === "ArrowDown") go(at + 1);
    else if (e.key === "ArrowUp") go(at - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(buttons.length - 1);
    else if (e.key === "Escape") onClose(true);
    else if (e.key === "Tab") return onClose(false);
    else return;
    e.preventDefault();
    e.stopPropagation();
  }

  return (
    <div ref={ref} className="menu" role="menu" aria-label={label} style={style} onKeyDown={onKeyDown}>
      {items.map((item, i) =>
        item.separator ? (
          <div key={i} className="menu-line" role="separator" />
        ) : (
          <button
            key={i}
            type="button"
            role={item.checked === undefined ? "menuitem" : "menuitemradio"}
            aria-checked={item.checked}
            className={`menu-item ${item.danger ? "danger" : ""}`}
            disabled={item.disabled}
            onClick={() => {
              onClose(true);
              item.onSelect?.();
            }}
          >
            <span className="menu-icon" aria-hidden>
              {item.checked ? "✓" : item.icon}
            </span>
            <span className="menu-label">{item.label}</span>
            {item.hint && (
              <span className="menu-hint" aria-hidden>
                {item.hint}
              </span>
            )}
          </button>
        ),
      )}
    </div>
  );
}

/** A button that opens a menu under itself. */
export function MenuButton({
  label,
  icon,
  text,
  items,
  className = "tool",
  disabled,
}: {
  label: string;
  icon?: ReactNode;
  text?: ReactNode;
  items: MenuItem[];
  className?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  return (
    <span className="menu-anchor">
      <button
        ref={button}
        type="button"
        className={className}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={text ? undefined : label}
        title={text ? undefined : label}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
      >
        {icon}
        {text}
      </button>
      {open && (
        <MenuList
          items={items}
          label={label}
          ignore={button}
          onClose={(refocus) => {
            setOpen(false);
            if (refocus) button.current?.focus();
          }}
        />
      )}
    </span>
  );
}

/** A menu at a point on the page (a right-click, or the "more" button of a row), kept on screen. */
export function ContextMenu({
  x,
  y,
  items,
  label,
  onClose,
}: {
  x: number;
  y: number;
  items: MenuItem[];
  label: string;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const opener = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  const [at, setAt] = useState({ left: x, top: y });

  useLayoutEffect(() => {
    const box = ref.current; // its laid-out size: the opening animation scales what is drawn
    if (!box) return;
    setAt({
      left: Math.max(8, Math.min(x, window.innerWidth - box.offsetWidth - 8)),
      top: Math.max(8, Math.min(y, window.innerHeight - box.offsetHeight - 8)),
    });
  }, [x, y]);

  return (
    <MenuList
      listRef={ref}
      items={items}
      label={label}
      style={{ position: "fixed", left: at.left, top: at.top }}
      onClose={(refocus) => {
        onClose();
        if (refocus && opener.current?.isConnected) opener.current.focus();
      }}
    />
  );
}
