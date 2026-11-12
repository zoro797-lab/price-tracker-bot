"""خدمة المنتجات: إنشاء/تحديث منتج مشترك بين المستخدمين."""
from __future__ import annotations

from app.database import Product, db_session
from app.providers import ProductSnapshot
from app.utils.formatting import localize, now


def get_or_create_product(snapshot: ProductSnapshot, url: str) -> tuple[Product, bool]:
    """منع تكرار المنتج: نفس المتجر + نفس المعرف الخارجي = منتج واحد مشترك."""
    with db_session() as session:
        product = None
        if snapshot.external_id:
            product = (
                session.query(Product)
                .filter(Product.store == snapshot.store,
                        Product.external_product_id == snapshot.external_id)
                .first()
            )
        if product is None:
            product = Product(
                store=snapshot.store,
                external_product_id=snapshot.external_id,
                product_url=url,
                title=snapshot.title,
                image_url=snapshot.image_url,
                currency=snapshot.currency,
                current_price=snapshot.price,
                availability_status=snapshot.availability.value,
                data_source=snapshot.data_source,
                last_checked_at=localize(now()),
                created_at=localize(now()),
            )
            session.add(product)
            session.flush()
            session.refresh(product)
            return product, True
        # تحديث بيانات المنتج الموجود بآخر قراءة موثوقة
        product.product_url = url or product.product_url
        product.title = snapshot.title or product.title
        product.image_url = snapshot.image_url or product.image_url
        product.current_price = snapshot.price if snapshot.price is not None else product.current_price
        product.availability_status = snapshot.availability.value
        product.data_source = snapshot.data_source
        product.last_checked_at = localize(now())
        return product, False


def get_product(product_id: int) -> Product | None:
    with db_session() as session:
        return session.query(Product).filter(Product.id == product_id).first()


def update_product_price(product_id: int, price: float, availability: str, data_source: str | None) -> None:
    with db_session() as session:
        product = session.query(Product).filter(Product.id == product_id).first()
        if product:
            product.current_price = price
            product.availability_status = availability
            product.data_source = data_source
            product.last_checked_at = localize(now())


def list_products(limit: int = 50) -> list[Product]:
    with db_session() as session:
        return session.query(Product).order_by(Product.created_at.desc()).limit(limit).all()
