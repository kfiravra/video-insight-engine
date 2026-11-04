// Query key factory for type-safe cache management
export const queryKeys = {
  // Videos
  videos: {
    all: ["videos"] as const,
    lists: () => [...queryKeys.videos.all, "list"] as const,
    list: (folderId?: string) =>
      [...queryKeys.videos.lists(), { folderId }] as const,
    details: () => [...queryKeys.videos.all, "detail"] as const,
    detail: (id: string) => [...queryKeys.videos.details(), id] as const,
  },

  // What a submit would hold, by URL. Its own root: invalidating video lists
  // or details never refetches estimates.
  videoEstimates: {
    all: ["video-estimate"] as const,
    byUrl: (url: string) => [...queryKeys.videoEstimates.all, url] as const,
  },

  // Folders
  folders: {
    all: ["folders"] as const,
    lists: () => [...queryKeys.folders.all, "list"] as const,
    list: () => [...queryKeys.folders.lists()] as const,
    details: () => [...queryKeys.folders.all, "detail"] as const,
    detail: (id: string) => [...queryKeys.folders.details(), id] as const,
  },

  // Playlists
  playlists: {
    all: ["playlists"] as const,
    videos: (playlistId: string) =>
      [...queryKeys.playlists.all, "videos", playlistId] as const,
  },

  // Auth
  auth: {
    all: ["auth"] as const,
    registration: () => [...queryKeys.auth.all, "registration"] as const,
  },

  // User
  user: {
    current: ["user", "current"] as const,
  },
} as const;
