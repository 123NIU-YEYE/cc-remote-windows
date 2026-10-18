import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AskOption, Engine } from "../protocol";
import { EngineIcon } from "../icons";
import { useImeSubmit } from "../use-ime-submit";
import "./QuestionSheet.css";

interface Props {
  engine: Engine;
  question: string;
  header?: string | null;
  options: AskOption[];
  allowText?: boolean;
  secret?: boolean;
  multiSelect?: boolean;
  onAnswer: (answer: string | string[]) => void;
}

export function QuestionSheet({
  engine, question, header, options, allowText, secret, multiSelect, onAnswer,
}: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const focusTextOnOpen = useRef(allowText && options.length === 0);
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const imeSubmit = useImeSubmit<HTMLInputElement>((value) => {
    if (!value.trim()) return;
    onAnswer(multiSelect ? [...selected, value] : value);
  });
  const toggle = (label: string) => {
    setSelected((current) => current.includes(label)
      ? current.filter((value) => value !== label)
      : [...current, label]);
  };
  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    dialog.showModal();
    // Keep the original text-only autofocus; choice questions open without a keyboard.
    const target = focusTextOnOpen.current ? imeSubmit.inputRef.current : headingRef.current;
    target?.focus({ preventScroll: true });
    return () => { dialog.close(); };
  }, [imeSubmit.inputRef]);
  useLayoutEffect(() => {
    const body = scrollRef.current;
    if (!body) return;
    const observer = new ResizeObserver(() => {
      const input = imeSubmit.inputRef.current;
      if (!input || document.activeElement !== input) return;
      const viewport = body.getBoundingClientRect();
      const editor = input.getBoundingClientRect();
      if (editor.height > viewport.height) return;
      const margin = Math.min(4, (viewport.height - editor.height) / 2);
      if (editor.bottom > viewport.bottom - margin) {
        body.scrollTop += Math.ceil(editor.bottom - viewport.bottom + margin);
      } else if (editor.top < viewport.top + margin) {
        body.scrollTop -= Math.ceil(viewport.top - editor.top + margin);
      }
    });
    observer.observe(body);
    return () => observer.disconnect();
  }, [imeSubmit.inputRef]);
  const content = (
      <dialog ref={dialogRef} className="question-dialog" aria-label="操作确认" aria-modal="true"
        onCancel={(event) => event.preventDefault()}
        onKeyDown={(event) => event.stopPropagation()} data-lock-horizontal-swipe>
        <div ref={headingRef} tabIndex={-1} className="question-dialog-title">
          <EngineIcon engine={engine} size={26} />
          <span>{engine === "claude" ? "Claude" : "Codex"} 想确认一下</span>
        </div>
        <div ref={scrollRef} className="sheet-scroll">
          {header && <div className="qa-header">{header}</div>}
          <div className="qa-question">{question}</div>
          <div className="qa-options">
            {options.map((o, i) => (
              <button key={i}
                className={`qa-opt${selected.includes(o.label) ? " selected" : ""}`}
                aria-pressed={multiSelect ? selected.includes(o.label) : undefined}
                onClick={() => multiSelect ? toggle(o.label) : onAnswer(o.label)}>
                <span className="qa-opt-label">{o.label}</span>
                {o.ds && <span className="qa-opt-ds">{o.ds}</span>}
              </button>
            ))}
            {multiSelect && (
              <button className="qa-multi-submit" disabled={selected.length === 0}
                onClick={() => onAnswer(selected)}>确定</button>
            )}
          </div>
          {allowText && <div className="qa-text-answer">
            <input ref={imeSubmit.inputRef} type={secret ? "password" : "text"} value={text}
              autoFocus={options.length === 0} placeholder={secret ? "输入敏感内容" : "输入回答"}
              onChange={(e) => setText(e.target.value)}
              onCompositionStart={imeSubmit.startComposition}
              onCompositionEnd={(e) => {
                imeSubmit.endComposition();
                setText(e.currentTarget.value);
              }}
              onKeyDown={(e) => {
                if (!imeSubmit.shouldSubmitKey({
                  key: e.key,
                  shiftKey: e.shiftKey,
                  isComposing: e.nativeEvent.isComposing,
                  keyCode: e.nativeEvent.keyCode,
                })) return;
                e.preventDefault();
                imeSubmit.requestSubmit();
              }} />
            <button disabled={!text.trim()}
              onPointerDown={imeSubmit.commitCompositionBeforePointerSubmit}
              onClick={imeSubmit.requestSubmit}>确定</button>
          </div>}
        </div>
      </dialog>
  );
  return typeof document === "undefined" ? content : createPortal(content, document.body);
}
