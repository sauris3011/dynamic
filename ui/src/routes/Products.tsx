import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import {
  Card,
  DataTable,
  Empty,
  ErrorNote,
  KeyValue,
  SearchBox,
  SectionTitle,
  Spinner,
  Stat,
} from '../components/primitives';
import { api } from '../lib/api';
import { datetime, money, num, ratio } from '../lib/format';
import { CATEGORIES } from '../lib/labels';
import { useApi } from '../lib/hooks';
import type { ProductDetail } from '../lib/types';

/**
 * Look up any product.
 *
 * Master/detail. Search is server-side and matches SKU, name, brand, category,
 * subcategory and family. A row opens on **double-click** — single click only
 * highlights, so scanning down the list with the keyboard or the mouse does not
 * fire a request per row. Enter opens a focused row too, so the interaction is
 * not mouse-only.
 */
export function Products() {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [highlighted, setHighlighted] = useState<string | null>(null);

  // The opened product lives in the URL, not in state: it makes the view
  // shareable, survives the back button, and lets the assistant panel see which
  // product is on screen without this component plumbing it anywhere.
  const [params, setParams] = useSearchParams();
  const openSku = params.get('sku');

  const products = useApi(
    () => api.products({ search, category: category || undefined, limit: 500 }),
    [search, category],
  );
  const detail = useApi(
    () => (openSku ? api.product(openSku) : Promise.resolve(null)),
    [openSku],
  );

  // Is there a live recommendation for the product on screen? This is the link
  // between looking something up and doing something about it, which the two
  // screens previously had no way to express.
  const pending = useApi(() => api.recommendations({ status: 'pending', limit: 5000 }), []);
  const recForOpen = useMemo(
    () => (openSku ? (pending.data ?? []).find((r) => r.sku === openSku) : undefined),
    [pending.data, openSku],
  );

  const open = (sku: string) => {
    setHighlighted(sku);
    setParams({ sku }, { replace: true });
  };

  return (
    <div className="p-7 space-y-5">
      <div className="flex items-start gap-6">
        <SectionTitle
          eyebrow="Products"
          title="Look up any product"
          lede="Search by code, name, brand or category. Double-click a row to see its
                full detail — price, cost, stock and recent sales."
        />
        <div className="ml-auto flex gap-2.5 shrink-0">
          <SearchBox
            value={search}
            onChange={setSearch}
            placeholder="Search products"
            className="w-72"
          />
          <label className="sr-only" htmlFor="cat">
            Category
          </label>
          <select
            id="cat"
            className="input w-44"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="">All categories</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
      </div>

      {products.error && <ErrorNote>{products.error}</ErrorNote>}

      <div className="grid grid-cols-[24rem_minmax(0,1fr)] gap-5 items-start">
        <Card
          title="Matches"
          right={<span className="label">{num(products.data?.length ?? 0)}</span>}
        >
          {products.loading && !products.data ? (
            <Spinner label="Loading" />
          ) : products.data?.length ? (
            <DataTable
              maxHeight="34rem"
              head={
                <>
                  <th className="py-2">Product</th>
                  <th className="text-right">Price</th>
                </>
              }
            >
              {products.data.map((product) => (
                <tr
                  key={product.sku}
                  tabIndex={0}
                  onClick={() => setHighlighted(product.sku)}
                  onDoubleClick={() => open(product.sku)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') open(product.sku);
                  }}
                  className={`border-b border-hairline last:border-0 cursor-pointer
                              outline-none focus:ring-1 focus:ring-info ${
                                openSku === product.sku
                                  ? 'bg-info-wash'
                                  : highlighted === product.sku
                                    ? 'bg-line/25'
                                    : 'hover:bg-line/15'
                              }`}
                >
                  <td className="py-2.5 min-w-0 max-w-0">
                    <div className="text-xs font-semibold text-ink truncate">
                      {product.name}
                    </div>
                    <div className="text-tiny text-faint font-mono">{product.sku}</div>
                    <div className="text-tiny text-muted">
                      {product.brand} · {product.category}
                    </div>
                  </td>
                  <td className="text-right text-xs text-ink tabular-nums align-top pt-2.5">
                    {money(product.current_price)}
                  </td>
                </tr>
              ))}
            </DataTable>
          ) : (
            !products.loading && <Empty>No products match that search.</Empty>
          )}
          <p className="text-micro text-faint mt-2.5">Double-click a row to open it.</p>
        </Card>

        <div className="min-w-0">
          {!openSku ? (
            <Empty>Double-click a product on the left to see its details.</Empty>
          ) : detail.loading && !detail.data ? (
            <Spinner label="Loading product" />
          ) : detail.error ? (
            <ErrorNote>{detail.error}</ErrorNote>
          ) : detail.data ? (
            <Detail
              data={detail.data}
              onOpenRecommendation={
                recForOpen ? () => navigate(`/review/${recForOpen.rec_id}`) : undefined
              }
              proposedPrice={recForOpen?.recommended_price}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function Detail({
  data,
  onOpenRecommendation,
  proposedPrice,
}: {
  data: ProductDetail;
  onOpenRecommendation?: () => void;
  proposedPrice?: number;
}) {
  const { product, inventory, recent_sales: sales } = data;
  const margin = (product.current_price - product.unit_cost) / product.current_price;
  const recentUnits = sales.reduce((total, row) => total + row.units, 0);
  const recentRevenue = sales.reduce((total, row) => total + row.revenue, 0);

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-4">
        <div className="min-w-0">
          <div className="label font-mono">{product.sku}</div>
          <h2 className="text-xl font-bold text-ink mt-1">{product.name}</h2>
          <p className="text-xs text-faint mt-1">
            {product.brand} · {product.category} / {product.subcategory}
          </p>
        </div>
        {onOpenRecommendation && (
          <button className="btn-primary ml-auto shrink-0" onClick={onOpenRecommendation}>
            See the proposed price{proposedPrice ? ` (${money(proposedPrice)})` : ''} →
          </button>
        )}
      </div>

      <div className="grid grid-cols-4 gap-3">
        <Stat label="Price today" value={money(product.current_price)} accent="accent" />
        <Stat label="Margin" value={ratio(margin, 1)} detail="of each sale we keep" />
        <Stat
          label="In stock"
          value={num(inventory.on_hand)}
          detail={`${inventory.cover_days.toFixed(1)} days of cover`}
        />
        <Stat
          label="Recent revenue"
          value={money(recentRevenue)}
          detail={`${num(recentUnits)} units over ${sales.length} days`}
        />
      </div>

      <div className="grid grid-cols-2 gap-5">
        <Card title="Catalog">
          <KeyValue
            rows={[
              ['Code', <span className="font-mono">{product.sku}</span>],
              ['Brand', product.brand],
              ['Category', product.category],
              ['Subcategory', product.subcategory],
              ['Product family', <span className="font-mono">{product.family_id}</span>],
              ['Size', `${product.size_value} ${product.size_unit}`],
              ['Launched', product.launched_on],
            ]}
          />
        </Card>

        <Card title="Price and stock">
          <KeyValue
            rows={[
              ['What it costs us', money(product.unit_cost)],
              [
                'Supplier floor',
                product.map_price === null ? 'none agreed' : money(product.map_price),
              ],
              ['List price', money(product.list_price)],
              ['Price today', money(product.current_price)],
              ['On order', num(inventory.on_order)],
              ['Sells per week', num(inventory.weekly_velocity)],
              ['Stock last checked', datetime(inventory.updated_at)],
            ]}
          />
        </Card>
      </div>

      <Card
        title="Recent sales"
        right={<span className="label">last {sales.length} days</span>}
      >
        {sales.length ? (
          <DataTable
            maxHeight="20rem"
            head={
              <>
                <th className="py-2">Date</th>
                <th className="text-right">Units</th>
                <th className="text-right">Price</th>
                <th className="text-right">Revenue</th>
                <th className="text-right">On promotion</th>
              </>
            }
          >
            {sales.map((row) => (
              <tr key={row.sale_date} className="border-b border-hairline last:border-0">
                <td className="py-2 text-muted">{row.sale_date}</td>
                <td className="text-right tabular-nums">{num(row.units)}</td>
                <td className="text-right tabular-nums">{money(row.unit_price)}</td>
                <td className="text-right tabular-nums">{money(row.revenue)}</td>
                <td className="text-right text-faint">{row.on_promo ? 'yes' : 'no'}</td>
              </tr>
            ))}
          </DataTable>
        ) : (
          <Empty>No sales recorded for this product.</Empty>
        )}
      </Card>
    </div>
  );
}
