import { useEffect, useState } from "react";
import { Icon } from "../icons";
import { openMedia, releaseMedia, type MediaResource, type MediaSource } from "../media";

export function MediaDownloadButton({ source }: { source: MediaSource }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return <span className="media-download-control">
    <button type="button" className="iconbtn" disabled={busy}
      title={error || "下载原文件"} aria-label="下载原文件" onClick={async () => {
        setBusy(true); setError("");
        try {
          const resource = await openMedia(source, true);
          const link = document.createElement("a");
          link.href = resource.url; link.download = resource.name;
          document.body.append(link); link.click(); link.remove();
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "下载失败，请重试");
        } finally { setBusy(false); }
      }}><Icon name={busy ? "refresh" : "download"} size={17} /></button>
    {error && <span className="media-action-error" role="alert">{error}</span>}
  </span>;
}

export default function LocalMediaPreview({ source, kind, title }: {
  source: MediaSource; kind: "video" | "audio"; title: string;
}) {
  const [resource, setResource] = useState<MediaResource | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const { machineId, sid, path, engine, space } = source;
  useEffect(() => {
    const controller = new AbortController();
    let opened: MediaResource | undefined;
    setResource(null); setError("");
    void openMedia({ machineId, sid, path, engine, space }, false, controller.signal).then(value => {
      opened = value;
      if (controller.signal.aborted) releaseMedia(value);
      else setResource(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "文件读取失败");
    });
    return () => { controller.abort(); if (opened) releaseMedia(opened); };
  }, [machineId, sid, path, engine, space, attempt]);
  if (error) return <div className="preview-error media-error">
    <p>{error}</p>
    <div><button type="button" onClick={() => setAttempt(value => value + 1)}>重试</button>
      <MediaDownloadButton source={source} /><span>也可以下载后在本机打开</span></div>
  </div>;
  if (!resource) return <div className="diff-empty" role="status">正在打开{kind === "video" ? "视频" : "音频"}…</div>;
  const failed = () => setError("浏览器无法播放此文件；可能是编码不受支持或连接已中断");
  return <div className={kind === "video" ? "artifact-video-stage" : "artifact-audio-stage"}>
    {kind === "video"
      ? <video key={resource.id} src={resource.url} controls playsInline preload="metadata"
          aria-label={title} onError={failed} />
      : <audio key={resource.id} src={resource.url} controls preload="metadata"
          aria-label={title} onError={failed} />}
    <span className="media-file-info">{resource.name} · {(resource.size / 1024 / 1024).toFixed(1)} MB</span>
  </div>;
}
