-- Ручное начальное заполнение пробного прода. Выполнено 4 октября 2026 на p-common-1.
-- Запустить после migrate up от имени владельца БД controlplane.
-- Общая ВМ не является контентным шардом: площадки и шарды пока не регистрируем.
begin;
insert into country (code, name, residency_note)
values ('RU', 'Россия', 'Пробное размещение в Yandex Cloud; коммерческие обязательства не объявлены')
on conflict (code) do nothing;
insert into region (code, country_code, name, sellable)
values ('ru-central1', 'RU', 'Yandex Cloud, ru-central1', false)
on conflict (code) do nothing;
commit;

select code, name, residency_note from country where code = 'RU';
select code, country_code, name, sellable from region where code = 'ru-central1';
-- PKI, бюллетень, номерной блок Accounts и правила размещения — отдельные шаги:
-- они не подменяются фиктивными записями о ещё не созданных контентных шардах.
