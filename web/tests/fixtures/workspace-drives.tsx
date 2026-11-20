import { useRef, useState } from "react";
import { DirPicker } from "../../src/components/DirPicker";
import { WINDOWS_DRIVES_PATH } from "../../src/directory-picker";

export function WorkspaceDrivesFixture() {
  const [listing, setListing] = useState({ path: "C:\\", parent: WINDOWS_DRIVES_PATH,
    dirs: [{ name: "Users", path: "C:\\Users" }], response: "initial" });
  const [confirmed, setConfirmed] = useState("");
  const [request, setRequest] = useState("");
  const nextRequest = useRef(0);
  return <>
    <output data-testid="drive-picker-confirmed">{confirmed}</output>
    <output data-testid="drive-picker-request">{request}</output>
    <DirPicker open path={listing.path} parent={listing.parent} dirs={listing.dirs}
      responseRequestId={listing.response} onClose={() => {}} onConfirm={setConfirmed}
      onBrowse={path => {
        const id = `browse-${++nextRequest.current}`;
        const target = path ?? "C:\\";
        setRequest(target);
        window.setTimeout(() => {
          const drives = target === WINDOWS_DRIVES_PATH;
          setListing({ path: target, parent: drives ? "" : target === "D:\\Projects" ? "D:\\" : WINDOWS_DRIVES_PATH,
            dirs: drives ? ["C:\\", "D:\\"].map(path => ({ name: path, path }))
              : target === "D:\\" ? [{ name: "Projects", path: "D:\\Projects" }] : [], response: id });
        }, 40);
        return id;
      }} />
  </>;
}
