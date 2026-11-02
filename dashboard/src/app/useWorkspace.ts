import { useEffect, useState } from "react";

export const workspaces = ["mission", "library", "create", "platforms", "history", "artifacts"] as const;
export type WorkspaceId = typeof workspaces[number];

function readWorkspace(hash: string): WorkspaceId {
  const candidate = hash.replace(/^#/, "");
  return workspaces.includes(candidate as WorkspaceId) ? candidate as WorkspaceId : "mission";
}

export function useWorkspace(): WorkspaceId {
  const [workspace, setWorkspace] = useState<WorkspaceId>(() => readWorkspace(window.location.hash));

  useEffect(() => {
    const update = () => setWorkspace(readWorkspace(window.location.hash));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  return workspace;
}
