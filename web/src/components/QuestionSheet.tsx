import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AskOption, AskQuestion, Engine } from "../protocol";
import { EngineIcon } from "../icons";
import { useImeSubmit } from "../use-ime-submit";
import { QuestionBatchSheet } from "./QuestionBatchSheet";
import "./QuestionSheet.css";

export interface QuestionDraft {
  text: string;
  selected: string[];
  scrollTop: number;
  page?: number;
  pages?: { text: string; selected: string[]; scrollTop: number }[];
  signature?: string;
}

interface Props {
  engine: Engine;
  question: string;
  header?: string | null;
  options: AskOption[];
  allowText?: boolean;
  secret?: boolean;
  multiSelect?: boolean;
  questions?: AskQuestion[] | null;
  draft?: { key: string; store: Map<string, QuestionDraft> };
  onAnswer: (answer: string | string[]) => boolean | void;
  onAnswers?: (answers: (string | string[])[]) => boolean | void;
}

export function QuestionSheet(props: Props) {
  return props.questions?.length ? <QuestionBatchSheet key={JSON.stringify(props.questions)}
    engine={props.engine} questions={props.questions} draft={props.draft}
    onSubmit={props.onAnswers ?? (() => false)} /> : <SingleQuestionSheet {...props} />;
}

function SingleQuestionSheet({
  engine, question, header, options, allowText, secret, multiSelect, onAnswer, draft,
}: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [initialDraft] = useState(() => draft?.store.get(draft.key));
  const focusTextOnOpen = useRef(!initialDraft && allowText && options.length === 0);
  const [text, setText] = useState(initialDraft?.text ?? "");
  const [selected, setSelected] = useState<string[]>(initialDraft?.selected ?? []);
  const saveDraft = (next: Partial<QuestionDraft>) => {
    if (!draft) return;
    const value: QuestionDraft = { text, selected,
      scrollTop: scrollRef.current?.scrollTop ?? 0, ...next };
    draft.store.delete(draft.key);
    draft.store.set(draft.key, value);
    // Only in-memory UI state; never persist answers (including secrets).
    while (draft.store.size > 64) draft.store.delete(draft.store.keys().next().value!);
  };
  const updateText = (value: string) => { setText(value); saveDraft({ text: value }); };
  const answer = (value: string | string[]) => {
    if (onAnswer(value) !== false && draft) draft.store.delete(draft.key);
  };
  const imeSubmit = useImeSubmit<HTMLInputElement>((value) => {
    if (!value.trim()) return;
    answer(multiSelect ? [...selected, value] : value);
  });
  const toggle = (label: string) => {
    const next = selected.includes(label)
      ? selected.filter((value) => value !== label) : [...selected, label];
    setSelected(next);
    saveDraft({ selected: next });
  };
  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    dialog.showModal();
    // Keep the original text-only autofocus; choice questions open without a keyboard.
    const target = focusTextOnOpen.current ? imeSubmit.inputRef.current : headingRef.current;
    target?.focus({ preventScroll: true });
    if (scrollRef.current && initialDraft) scrollRef.current.scrollTop = initialDraft.scrollTop;
    return () => { dialog.close(); };
  }, [imeSubmit.inputRef, initialDraft]);
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
        <div ref={scrollRef} className="sheet-scroll" onScroll={() => saveDraft({})}>
          {header && <div className="qa-header">{header}</div>}
          <div className="qa-question">{question}</div>
          <div className="qa-options">
            {options.map((o, i) => (
              <button key={i}
                className={`qa-opt${selected.includes(o.label) ? " selected" : ""}`}
                aria-pressed={multiSelect ? selected.includes(o.label) : undefined}
                onClick={() => multiSelect ? toggle(o.label) : answer(o.label)}>
                <span className="qa-opt-label">{o.label}</span>
                {o.ds && <span className="qa-opt-ds">{o.ds}</span>}
              </button>
            ))}
            {multiSelect && (
              <button className="qa-multi-submit" disabled={selected.length === 0}
                onClick={() => answer(selected)}>确定</button>
            )}
          </div>
          {allowText && <div className="qa-text-answer">
            <input ref={imeSubmit.inputRef} type={secret ? "password" : "text"} value={text}
              autoFocus={focusTextOnOpen.current} placeholder={secret ? "输入敏感内容" : "输入回答"}
              onChange={(e) => updateText(e.target.value)}
              onCompositionStart={imeSubmit.startComposition}
              onCompositionEnd={(e) => {
                imeSubmit.endComposition();
                updateText(e.currentTarget.value);
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
