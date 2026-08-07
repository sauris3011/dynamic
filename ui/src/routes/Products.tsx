import { FormEvent, useMemo, useState } from 'react';
import { Search } from 'lucide-react';

import { Card, Empty, ErrorNote, KeyValue, SectionTitle, Spinner, Stat } from '../components/primitives';
import { api } from '../lib/api';
import { money, num } from '../lib/format';
import { useApi } from '../lib/hooks';

export function Products() {
  const [draft, setDraft] = useState('');
  const [search, setSearch] = useState('');
  const products = useApi(() => api.products({ search, limit: 500 }), [search]);
  const [selectedSku, setSelectedSku] = useState<string | null>(null);
  const detail = useApi(
    () => selectedSku ? api.product(selectedSku) : Promise.resolve(null),
    [selectedSku],
  );

  const selected = detail.data;
  const margin = selected
    ? (selected.product.current_price - selected.product.unit_cost) / selected.product.current_price
    : 0;
  const sales = useMemo(() => selected?.recent_sales ?? [], [selected]);
  const recentUnits = sales.reduce((total, row) => total + row.units, 0);
  const recentRevenue = sales.reduce((total, row) => total + row.revenue, 0);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSearch(draft.trim());
  };

  return (
    <div className="p-7 space-y-5">
      <div className="flex items-start gap-6">
        <SectionTitle
          eyebrow="Catalog · Product intelligence"
          title="Product details"
          lede="Search by SKU, product name, brand, category, subcategory, or family, then inspect live commercial and inventory data."
        />
        <form onSubmit={submit} className="ml-auto w-[420px] flex gap-2" role="search">
          <label className="relative flex-1">
            <Search size={14} className="absolute left-3 top-2.5 text-faint" aria-hidden />
            <span className="sr-only">Search products</span>
            <input
              className="input pl-9"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Search SKU, name, brand, category…"
            />
          </label>
          <button className="btn-primary" type="submit">Search</button>
        </form>
      </div>

      {products.error && <ErrorNote>{products.error}</ErrorNote>}
      <div className="grid grid-cols-[330px_minmax(0,1fr)] gap-5 items-start">
        <Card
          title="Products"
          right={<span className="label">{num(products.data?.length ?? 0)} matches</span>}
          className="min-h-[560px]"
        >
          {products.loading && !products.data ? <Spinner label="Loading catalog" /> : null}
          {products.data?.length ? (
            <div className="max-h-[620px] overflow-y-auto -mx-2">
              {products.data.map((product) => (
                <button
                  key={product.sku}
                  onClick={() => setSelectedSku(product.sku)}
                  className={`w-full text-left px-3 py-3 border-b border-hairline transition-colors ${
                    selectedSku === product.sku ? 'bg-info-wash' : 'hover:bg-line/20'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-ink truncate">{product.name}</span>
                    <span className="ml-auto text-xs text-ink">{money(product.current_price)}</span>
                  </div>
                  <div className="text-tiny text-faint mt-1 font-mono">{product.sku}</div>
                  <div className="text-tiny text-muted mt-0.5">{product.brand} · {product.category}</div>
                </button>
              ))}
            </div>
          ) : !products.loading ? <Empty>No products match this search.</Empty> : null}
        </Card>

        <div className="space-y-5 min-w-0">
          {!selectedSku ? (
            <Empty>Select a product from the search results to view its details.</Empty>
          ) : detail.loading && !selected ? (
            <Spinner label="Loading product details" />
          ) : detail.error ? (
            <ErrorNote>{detail.error}</ErrorNote>
          ) : selected ? (
            <>
              <div className="flex items-start">
                <div>
                  <div className="label font-mono">{selected.product.sku}</div>
                  <h2 className="text-xl font-bold text-ink mt-1">{selected.product.name}</h2>
                  <p className="text-xs text-faint mt-1">{selected.product.brand} · {selected.product.category} / {selected.product.subcategory}</p>
                </div>
              </div>
              <div className="grid grid-cols-4 gap-3">
                <Stat label="Current price" value={money(selected.product.current_price)} accent="accent" />
                <Stat label="Gross margin" value={`${(margin * 100).toFixed(1)}%`} />
                <Stat label="On hand" value={num(selected.inventory.on_hand)} detail={`${selected.inventory.cover_days.toFixed(1)} cover days`} />
                <Stat label="Recent revenue" value={money(recentRevenue)} detail={`${num(recentUnits)} units · ${sales.length} days`} />
              </div>
              <div className="grid grid-cols-2 gap-5">
                <Card title="Catalog fields">
                  <KeyValue rows={[
                    ['SKU', <span className="font-mono">{selected.product.sku}</span>],
                    ['Brand', selected.product.brand],
                    ['Category', selected.product.category],
                    ['Subcategory', selected.product.subcategory],
                    ['Family ID', <span className="font-mono">{selected.product.family_id}</span>],
                    ['Size', `${selected.product.size_value} ${selected.product.size_unit}`],
                    ['Launched', selected.product.launched_on],
                  ]} />
                </Card>
                <Card title="Price & inventory">
                  <KeyValue rows={[
                    ['Unit cost', money(selected.product.unit_cost)],
                    ['MAP price', selected.product.map_price === null ? '—' : money(selected.product.map_price)],
                    ['List price', money(selected.product.list_price)],
                    ['Current price', money(selected.product.current_price)],
                    ['On order', num(selected.inventory.on_order)],
                    ['Weekly velocity', num(selected.inventory.weekly_velocity)],
                    ['Inventory updated', new Date(selected.inventory.updated_at).toLocaleString()],
                  ]} />
                </Card>
              </div>
              <Card title="Recent sales" right={<span className="label">Latest {sales.length} records</span>}>
                {sales.length ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead className="label text-left"><tr><th className="pb-2">Date</th><th>Units</th><th>Unit price</th><th>Revenue</th><th>Promotion</th></tr></thead>
                      <tbody>{sales.map((row) => (
                        <tr key={row.sale_date} className="border-t border-hairline">
                          <td className="py-2 text-muted">{row.sale_date}</td><td>{num(row.units)}</td><td>{money(row.unit_price)}</td><td>{money(row.revenue)}</td><td>{row.on_promo ? 'Yes' : 'No'}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                ) : <Empty>No sales history is available for this product.</Empty>}
              </Card>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
