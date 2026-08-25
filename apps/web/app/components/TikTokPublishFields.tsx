"use client";

import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { SocialAccount, TikTokCreatorInfo } from "@/lib/types";

// TikTok's UX requirements for third-party publishing (queried creator
// settings, no preselected privacy, explicit AI disclosure) -- extracted
// from GenerationForm's ReviewAndPublish (CIN-136) so the video studio's
// publish block enforces exactly the same compliance rules.

export interface TikTokPostOptions {
  mode: "direct_post" | "draft_upload";
  privacy_level: string;
  disable_comment: boolean;
  disable_duet: boolean;
  disable_stitch: boolean;
  brand_content_toggle: boolean;
  brand_organic_toggle: boolean;
  is_aigc: boolean;
}

const TIKTOK_PRIVACY_LABELS: Record<string, string> = {
  PUBLIC_TO_EVERYONE: "Все",
  MUTUAL_FOLLOW_FRIENDS: "Друзья (взаимные подписки)",
  FOLLOWER_OF_CREATOR: "Подписчики",
  SELF_ONLY: "Только я",
};

export function useTikTokPublishOptions(
  targetTikTokAccounts: SocialAccount[],
  token: string | null,
  { aiGenerated }: { aiGenerated: boolean }
) {
  const [creators, setCreators] = useState<Record<string, TikTokCreatorInfo>>({});
  const [options, setOptions] = useState<Record<string, TikTokPostOptions>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (targetTikTokAccounts.length === 0) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all(
      targetTikTokAccounts.map(async (account) => {
        const creator = await api.get<TikTokCreatorInfo>(
          `/social-accounts/${account.id}/tiktok/creator-info`,
          token
        );
        return { account, creator };
      })
    )
      .then((results) => {
        if (cancelled) return;
        setCreators((previous) => {
          const next = { ...previous };
          for (const { account, creator } of results) next[account.id] = creator;
          return next;
        });
        setOptions((previous) => {
          const next = { ...previous };
          for (const { account, creator } of results) {
            next[account.id] = next[account.id] ?? {
              mode: "direct_post",
              // TikTok explicitly forbids silently preselecting a
              // privacy level: the creator must choose every time.
              privacy_level: "",
              disable_comment: creator.comment_disabled,
              disable_duet: creator.duet_disabled,
              disable_stitch: creator.stitch_disabled,
              brand_content_toggle: false,
              brand_organic_toggle: false,
              is_aigc: aiGenerated,
            };
          }
          return next;
        });
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof ApiError ? err.message : "Не удалось получить настройки TikTok"
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [targetTikTokAccounts, token, aiGenerated]);

  function updateOption<K extends keyof TikTokPostOptions>(
    accountId: string,
    key: K,
    value: TikTokPostOptions[K]
  ) {
    setOptions((previous) => ({
      ...previous,
      [accountId]: { ...previous[accountId], [key]: value },
    }));
  }

  const ready =
    targetTikTokAccounts.length === 0 ||
    (targetTikTokAccounts.every((account) => {
      const accountOptions = options[account.id];
      return (
        creators[account.id] &&
        accountOptions &&
        (accountOptions.mode === "draft_upload" || Boolean(accountOptions.privacy_level))
      );
    }) &&
      !error);

  const platformOptions = useMemo(
    () =>
      targetTikTokAccounts.length === 0 ? {} : { tiktok: { accounts: options } },
    [targetTikTokAccounts, options]
  );

  return { creators, options, updateOption, error, loading, ready, platformOptions };
}

export function TikTokPublishFields({
  accounts,
  creators,
  options,
  onChange,
  loading,
  error,
  aigcLocked = false,
}: {
  accounts: SocialAccount[];
  creators: Record<string, TikTokCreatorInfo>;
  options: Record<string, TikTokPostOptions>;
  onChange: <K extends keyof TikTokPostOptions>(
    accountId: string,
    key: K,
    value: TikTokPostOptions[K]
  ) => void;
  loading: boolean;
  error: string | null;
  // true when the video is known to be AI-generated (Veo) -- the
  // disclosure checkbox is then forced on and not editable.
  aigcLocked?: boolean;
}) {
  return (
    <>
      {accounts.map((account) => {
        const creator = creators[account.id];
        const accountOptions = options[account.id];
        if (!creator || !accountOptions) return null;
        return (
          <fieldset key={account.id} className="card">
            <legend>TikTok — @{creator.creator_username || creator.creator_nickname}</legend>
            <p className="muted">
              Настройки получены прямо из TikTok. Максимальная длительность: {creator.max_video_post_duration_sec} сек.
            </p>
            <label>
              Способ отправки
              <select
                required
                value={accountOptions.mode}
                onChange={(event) =>
                  onChange(account.id, "mode", event.target.value as TikTokPostOptions["mode"])
                }
              >
                <option value="direct_post">Опубликовать напрямую</option>
                <option value="draft_upload">Отправить в TikTok как черновик</option>
              </select>
            </label>
            {accountOptions.mode === "draft_upload" ? (
              <p className="muted">
                Видео появится во входящих TikTok. Откройте уведомление в мобильном приложении,
                отредактируйте черновик и завершите публикацию там.
              </p>
            ) : (
              <>
                <label>
                  Кто увидит видео
                  <select
                    required
                    value={accountOptions.privacy_level}
                    onChange={(event) =>
                      onChange(account.id, "privacy_level", event.target.value)
                    }
                  >
                    <option value="">Выберите приватность</option>
                    {creator.privacy_level_options.map((level) => (
                      <option key={level} value={level}>
                        {TIKTOK_PRIVACY_LABELS[level] ?? level}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={accountOptions.disable_comment}
                    disabled={creator.comment_disabled}
                    onChange={(event) =>
                      onChange(account.id, "disable_comment", event.target.checked)
                    }
                  />
                  Отключить комментарии
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={accountOptions.disable_duet}
                    disabled={creator.duet_disabled}
                    onChange={(event) =>
                      onChange(account.id, "disable_duet", event.target.checked)
                    }
                  />
                  Отключить Duet
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={accountOptions.disable_stitch}
                    disabled={creator.stitch_disabled}
                    onChange={(event) =>
                      onChange(account.id, "disable_stitch", event.target.checked)
                    }
                  />
                  Отключить Stitch
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={accountOptions.brand_organic_toggle}
                    onChange={(event) =>
                      onChange(account.id, "brand_organic_toggle", event.target.checked)
                    }
                  />
                  Видео продвигает наш собственный бренд, продукт или услугу
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={accountOptions.brand_content_toggle}
                    onChange={(event) =>
                      onChange(account.id, "brand_content_toggle", event.target.checked)
                    }
                  />
                  Видео содержит платное продвижение стороннего бренда
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={accountOptions.is_aigc}
                    disabled={aigcLocked}
                    onChange={(event) => onChange(account.id, "is_aigc", event.target.checked)}
                  />
                  Контент создан с помощью ИИ
                </label>
              </>
            )}
          </fieldset>
        );
      })}
      {loading && <p className="muted">Загружаем актуальные настройки TikTok…</p>}
      {error && <p className="error">{error}</p>}
    </>
  );
}
