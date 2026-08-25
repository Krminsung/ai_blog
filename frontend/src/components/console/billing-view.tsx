"use client";

import { AsyncSection, PageHeader, StatCard } from "@/components/console/page-parts";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/surface";
import { Mono, Table, TableWrap, Td, Th, Tr } from "@/components/ui/table";
import { billing } from "@/lib/api/endpoints";
import { formatDate, formatDateTime, formatDecimal, humanizeEnum } from "@/lib/format";
import { useApi } from "@/lib/hooks/use-query";

/**
 * Billing. Money and credits are separate ledgers in the backend, so they are
 * never summed into a single "balance" here.
 */
export function BillingView() {
  const subscription = useApi("billing-subscription", () =>
    billing.subscription(),
  );
  const credits = useApi("billing-credits", () => billing.credits());
  const ledger = useApi("billing-ledger", () =>
    billing.creditLedger({ limit: 50 }),
  );
  const usage = useApi("billing-usage", () => billing.usageRecords({ limit: 50 }));

  return (
    <>
      <PageHeader
        title="요금과 사용량"
        description="구독 요금은 금전 원장으로, 생성·미디어·대량·재활용 사용량은 크레딧 원장으로 정산됩니다."
      />

      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="사용 가능 크레딧"
          value={
            credits.isLoading
              ? "—"
              : formatDecimal(credits.data?.available_balance, 0)
          }
        />
        <StatCard
          label="홀드된 크레딧"
          value={
            credits.isLoading ? "—" : formatDecimal(credits.data?.held_balance, 0)
          }
          hint="진행 중 작업이 잡아 둔 금액"
          tone="caution"
        />
        <StatCard
          label="구독 상태"
          value={
            subscription.isLoading
              ? "—"
              : humanizeEnum(subscription.data?.state ?? null)
          }
        />
        <StatCard
          label="다음 결제"
          value={
            subscription.isLoading
              ? "—"
              : formatDate(subscription.data?.current_period_end)
          }
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="구독"
            description="요금제와 구독은 버전으로 고정됩니다."
          />
          <CardBody>
            <AsyncSection
              data={subscription.data}
              error={subscription.error}
              errorText={subscription.errorText}
              isLoading={subscription.isLoading}
              onRetry={() => void subscription.mutate()}
              skeletonRows={3}
            >
              {(data) => (
                <dl className="flex flex-col gap-2.5 text-[13.5px]">
                  <Row term="요금제 버전">
                    <Mono>{data.plan_version_id.split("-")[0]}</Mono>
                  </Row>
                  <Row term="결제 주기">{humanizeEnum(data.billing_cycle)}</Row>
                  <Row term="현재 기간">
                    {formatDate(data.current_period_start)} –{" "}
                    {formatDate(data.current_period_end)}
                  </Row>
                  <Row term="체험 종료">{formatDate(data.trial_ends_at)}</Row>
                  <Row term="기간 종료 시 해지">
                    {data.cancel_at_period_end ? (
                      <Badge tone="caution">예정됨</Badge>
                    ) : (
                      "없음"
                    )}
                  </Row>
                  {data.scheduled_change_at ? (
                    <Row term="예약된 변경">
                      {formatDateTime(data.scheduled_change_at)}
                    </Row>
                  ) : null}
                </dl>
              )}
            </AsyncSection>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="크레딧 원장"
            description="홀드, 확정, 반환이 모두 기록됩니다."
          />
          <CardBody>
            <AsyncSection
              data={ledger.data}
              error={ledger.error}
              errorText={ledger.errorText}
              isLoading={ledger.isLoading}
              onRetry={() => void ledger.mutate()}
              isEmpty={(data) => data.length === 0}
              empty={{ title: "원장 기록이 없습니다" }}
              skeletonRows={4}
            >
              {(rows) => (
                <ul className="flex flex-col gap-1.5">
                  {rows.slice(0, 12).map((entry, index) => (
                    <li
                      key={String(entry.id ?? index)}
                      className="flex items-center justify-between gap-3 border-b border-[var(--hairline-soft)] py-2 text-[13px] last:border-b-0"
                    >
                      <span className="min-w-0">
                        <span className="block truncate">
                          {humanizeEnum(String(entry.entry_type ?? entry.kind ?? ""))}
                        </span>
                        <span className="type-caption">
                          {formatDateTime(String(entry.created_at ?? ""))}
                        </span>
                      </span>
                      <span className="numeric shrink-0 font-medium">
                        {formatDecimal(String(entry.amount ?? "0"), 0)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </AsyncSection>
          </CardBody>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader
          title="사용량"
          description="지표별 사용량과 원가 메타데이터입니다."
        />
        <CardBody>
          <AsyncSection
            data={usage.data}
            error={usage.error}
            errorText={usage.errorText}
            isLoading={usage.isLoading}
            onRetry={() => void usage.mutate()}
            isEmpty={(data) => data.length === 0}
            empty={{ title: "사용량 기록이 없습니다" }}
            skeletonRows={4}
          >
            {(rows) => (
              <TableWrap>
                <Table>
                  <thead>
                    <tr>
                      <Th>지표</Th>
                      <Th align="right">수량</Th>
                      <Th align="right">크레딧</Th>
                      <Th>상태</Th>
                      <Th align="right">발생</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((record) => (
                      <Tr key={record.id}>
                        <Td>
                          <Mono>{record.metric_key}</Mono>
                          {record.endpoint ? (
                            <p className="type-caption mt-0.5">
                              {record.endpoint}
                            </p>
                          ) : null}
                        </Td>
                        <Td align="right">
                          <span className="numeric">
                            {formatDecimal(record.quantity, 0)} {record.unit_name}
                          </span>
                        </Td>
                        <Td align="right">
                          <span className="numeric">
                            {formatDecimal(record.credit_amount, 0)}
                          </span>
                        </Td>
                        <Td>{humanizeEnum(record.state)}</Td>
                        <Td align="right">
                          <span className="text-[13px]">
                            {formatDateTime(record.occurred_at)}
                          </span>
                        </Td>
                      </Tr>
                    ))}
                  </tbody>
                </Table>
              </TableWrap>
            )}
          </AsyncSection>
        </CardBody>
      </Card>
    </>
  );
}

function Row({
  term,
  children,
}: {
  term: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-[var(--text-tertiary)]">{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}
