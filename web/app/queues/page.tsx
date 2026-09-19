import type { Metadata } from "next";
import { fetchReports } from "@/lib/api";
import { Queues } from "@/components/Queues";

export const metadata: Metadata = { title: "Queues" };
export const revalidate = 15;

export default async function QueuesPage() {
  const { items } = await fetchReports();
  return <Queues initial={items.filter((r) => r.status === "NEEDS_REVIEW")} />;
}
