import { PageHeadSkeleton, TableSkeleton } from "@/components/Skeleton";

export default function Loading() {
  return (<><PageHeadSkeleton /><div className="grid-2"><TableSkeleton rows={5} /><TableSkeleton rows={5} /></div></>);
}
