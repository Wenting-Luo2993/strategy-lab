export type ResearchArtifactClass =
  | "image"
  | "markdown"
  | "csv"
  | "json"
  | "text"
  | "pdf";

export type ResearchArtifactPreviewPolicy =
  | "inline"
  | "download"
  | "metadata_only"
  | "blocked";

export type ResearchArtifactMetadata = {
  artifactId: string;
  owningPublicationKey: string;
  sizeBytes: number;
  mediaType: string;
  displayName: string;
  artifactClass: ResearchArtifactClass;
  previewPolicy: ResearchArtifactPreviewPolicy;
  sanitizerVersion: number;
  manifestVersion: number;
  publishedAt: string;
};

export type ResearchArtifactPreview = {
  artifactId: string;
  url: string;
  expiresAt: string;
  mediaType: string;
  previewPolicy: "inline" | "download";
  displayName: string;
  sizeBytes: number;
};

export type ResearchArtifactAdapter = {
  readonly source: "local" | "supabase";
  listArtifacts(
    owningPublicationKey: string,
    page?: { limit?: number; offset?: number },
  ): Promise<ResearchArtifactMetadata[]>;
  getArtifact(artifactId: string): Promise<ResearchArtifactMetadata | null>;
  requestPreview(artifactId: string): Promise<ResearchArtifactPreview>;
};
