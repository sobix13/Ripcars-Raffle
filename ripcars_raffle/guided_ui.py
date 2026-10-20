"""Everyday raffle controls. Specialist settings remain in the existing panel."""
from __future__ import annotations

import copy
import csv
import io
import json
import time

import discord

from .admin_ui import SafeView,SafeModal,Setup,Hub,Manage,DraftDetails,Confirm,checked_save
from .operations import authorized,reply,doctor,attachment


def role_summary(cfg):
    values=[f'<@&{rid}>: {n} ticket(s)' for rid,n in cfg['role_tickets'].items()]
    return '\n'.join(values) or 'No ticket defaults yet. Add a role below. Unconfigured roles start with one ticket.'


def add_home(view,bot,user):
    button=discord.ui.Button(label='Home')
    async def run(i):
        await reply(i,'Raffle dashboard',view=Home(bot,user))
    button.callback=run;view.add_item(button)


class Home(SafeView):
    def __init__(self,bot,user):
        super().__init__(bot,user)
        for label,action in [('New raffle','new'),('Current raffle','current'),('Past raffles','past'),('Settings','settings')]:
            button=discord.ui.Button(label=label,style=discord.ButtonStyle.primary if action=='new' else discord.ButtonStyle.secondary)
            async def run(i,action=action):
                await i.response.defer(ephemeral=True)
                cfg,rev=await bot.db.settings(i.guild_id)
                await authorized(bot,i,action=='settings')
                if action=='new':
                    if not cfg.get('raffle_role'):
                        return await reply(i,'First, ask a server admin to finish Settings > Quick setup. It creates the fixed Raffle entry role.',view=Home(bot,user))
                    await reply(i,'Who can join? Select the allowed roles. Their tickets are added together.',view=NewRaffle(bot,user,cfg))
                elif action=='settings':
                    await reply(i,'Set up once. Ticket defaults are editable and affect new raffles only.',view=SettingsHome(bot,user,cfg,rev))
                else:
                    await show_archive(bot,i,action)
            button.callback=run;self.add_item(button)


class SettingsHome(SafeView):
    def __init__(self,bot,user,cfg,revision):
        super().__init__(bot,user,True)
        for label,action in [('Quick setup','setup'),('Ticket defaults','tickets'),('Check readiness','doctor'),('Advanced','advanced')]:
            button=discord.ui.Button(label=label)
            async def run(i,action=action):
                await i.response.defer(ephemeral=True)
                await authorized(bot,i,True)
                fresh,rev=await bot.db.settings(i.guild_id)
                if action=='setup':
                    await reply(i,'Choose the existing member role, managers and channels. Save to create your fixed Raffle entry role.',view=Setup(bot,user,fresh,rev))
                elif action=='tickets':
                    await reply(i,role_summary(fresh),view=TicketDefaults(bot,user,fresh,rev))
                elif action=='doctor':
                    blockers,_=await doctor(bot,i.guild,fresh)
                    await reply(i,'Ready to publish.' if not blockers else 'Finish these steps:\n'+'\n'.join(blockers),view=SettingsHome(bot,user,fresh,rev))
                else:
                    await reply(i,'Specialist settings and exports.',view=Hub(bot,user))
            button.callback=run;self.add_item(button)
        add_home(self,bot,user)


async def show_archive(bot,i,kind,before=0,previous=()):
    await authorized(bot,i)
    clause="status NOT IN ('drawn','cancelled')" if kind=='current' else "status IN ('drawn','cancelled')"
    rows=await bot.db.query(f'SELECT id,spec,status FROM raffles WHERE guild=? AND {clause} AND (?=0 OR id<?) ORDER BY id DESC LIMIT 26',(i.guild_id,before,before))
    if kind=='current' and len(rows)==1:
        from .admin_ui import show_raffle
        return await show_raffle(bot,i,rows[0]['id'])
    view=SafeView(bot,i.user.id)
    if rows:
        select=discord.ui.Select(placeholder='Choose a raffle',options=[discord.SelectOption(label=f"#{r['id']} {json.loads(r['spec'])['title']}"[:100],value=str(r['id']),description=r['status']) for r in rows[:25]])
        async def choose(j):
            await j.response.defer(ephemeral=True);await authorized(bot,j)
            from .admin_ui import show_raffle
            await show_raffle(bot,j,int(select.values[0]))
        select.callback=choose;view.add_item(select)
    if len(rows)>25:
        older=discord.ui.Button(label='Older raffles')
        async def next_page(j):
            await j.response.defer(ephemeral=True)
            await show_archive(bot,j,kind,rows[24]['id'],(*previous,before))
        older.callback=next_page;view.add_item(older)
    if previous:
        newer=discord.ui.Button(label='Newer raffles')
        async def prev_page(j):
            await j.response.defer(ephemeral=True)
            await show_archive(bot,j,kind,previous[-1],previous[:-1])
        newer.callback=prev_page;view.add_item(newer)
    add_home(view,bot,i.user.id)
    await reply(i,'Choose a raffle.' if rows else 'No raffles in this section yet.',view=view)


class TicketDefaults(SafeView):
    def __init__(self,bot,user,cfg,revision):
        super().__init__(bot,user,True)
        select=discord.ui.RoleSelect(placeholder='Choose a role to set its tickets',min_values=1,max_values=1)
        async def choose(i):
            rid=select.values[0].id
            if rid==cfg['raffle_role'] or select.values[0].is_default() or select.values[0].managed:
                return await reply(i,'Choose an ordinary qualifying role, not the temporary entry role.')
            await i.response.send_modal(TicketNumber(bot,user,cfg,revision,rid))
        select.callback=choose;self.add_item(select)
        suggested=discord.ui.Button(label='Preview starter weights')
        async def preview(i):
            candidate=copy.deepcopy(cfg)
            if cfg['member_role']:
                candidate['role_tickets'][str(cfg['member_role'])]=1
            matches=[r for r in i.guild.roles if r.name.casefold()=='og' and not r.managed]
            if len(matches)==1:
                candidate['role_tickets'][str(matches[0].id)]=3
            async def save(j):
                await checked_save(bot,j.guild,candidate,revision,j.user.id)
                current,rev=await bot.db.settings(j.guild_id)
                await reply(j,role_summary(current),view=TicketDefaults(bot,user,current,rev))
            await reply(i,role_summary(candidate)+'\nYou can edit every value later.',view=Confirm(bot,user,save,'Save starter weights',True))
        suggested.callback=preview;self.add_item(suggested)
        add_home(self,bot,user)


class TicketNumber(SafeModal):
    def __init__(self,bot,user,cfg,revision,rid,r=None):
        super().__init__(bot,user,'Tickets for this role',r is None)
        self.cfg,self.revision,self.rid=cfg,revision,rid
        self.r=r
        weights=r['spec']['rules']['weights'] if r else cfg['role_tickets']
        self.amount=discord.ui.TextInput(label='Number of tickets: 1 to 100',default=str(weights.get(str(rid),1)),max_length=3)
        self.add_item(self.amount)
    async def on_submit(self,i):
        await i.response.defer(ephemeral=True);await self.check(i)
        if not self.amount.value.isdecimal() or not 1<=int(self.amount.value)<=100:
            raise ValueError('Enter a whole number from 1 to 100 tickets.')
        if self.r:
            spec=copy.deepcopy(self.r['spec']);spec['rules']['weights'][str(self.rid)]=int(self.amount.value)
            await self.bot.db.edit(i.guild_id,self.r['id'],spec,self.r['revision'],i.user.id)
            from .admin_ui import show_raffle
            return await show_raffle(self.bot,i,self.r['id'])
        candidate=copy.deepcopy(self.cfg)
        candidate['role_tickets'][str(self.rid)]=int(self.amount.value)
        await checked_save(self.bot,i.guild,candidate,self.revision,i.user.id)
        current,rev=await self.bot.db.settings(i.guild_id)
        await reply(i,role_summary(current),view=TicketDefaults(self.bot,self.user,current,rev))


class NewRaffle(SafeView):
    def __init__(self,bot,user,cfg,r=None):
        super().__init__(bot,user)
        self.roles=discord.ui.RoleSelect(placeholder='Members with any selected role can join',min_values=1,max_values=20)
        if r:
            self.roles.default_values=[discord.SelectDefaultValue(id=x,type=discord.SelectDefaultValueType.role) for x in r['spec']['rules']['any_roles']]
        async def selected(i):
            selected=[x.id for x in self.roles.values]
            if cfg.get('raffle_role') in selected or any(x.managed or x.is_default() for x in self.roles.values):
                return await reply(i,'Choose member roles. The temporary Raffle role is not a qualifying role.')
            initial=copy.deepcopy(r['spec'] if r else cfg['templates']['general'])
            initial['rules'].update(any_roles=selected,all_roles=[],weights={str(x):cfg['role_tickets'].get(str(x),1) for x in selected},weight_mode='tickets',weight_cap=2000)
            if r:
                await i.response.defer(ephemeral=True);await authorized(bot,i)
                await bot.db.edit(i.guild_id,r['id'],initial,r['revision'],i.user.id)
                from .admin_ui import show_raffle
                await show_raffle(bot,i,r['id'])
            else:
                await i.response.send_modal(DraftDetails(bot,user,'general',initial=initial))
        self.roles.callback=selected;self.add_item(self.roles)
        add_home(self,bot,user)


class RaffleActions(SafeView):
    def __init__(self,bot,user,r):
        super().__init__(bot,user)
        if r['status']=='draft':
            for label,action in [('Edit prize and time','details'),('Who can join','roles'),('Edit tickets','tickets'),('Preview and publish','publish'),('More options','more')]:
                button=discord.ui.Button(label=label,style=discord.ButtonStyle.primary if action=='publish' else discord.ButtonStyle.secondary)
                async def run(i,action=action):
                    if action=='details':
                        return await i.response.send_modal(DraftDetails(bot,user,r['category'],r))
                    await i.response.defer(ephemeral=True);cfg=await authorized(bot,i)
                    if action=='roles':
                        return await reply(i,'Choose the allowed roles. Selected role tickets are added.',view=NewRaffle(bot,user,cfg,r))
                    if action=='tickets':
                        view=SafeView(bot,user)
                        ids=r['spec']['rules']['any_roles']
                        if not ids:
                            return await reply(i,'Choose the allowed roles first.')
                        select=discord.ui.Select(placeholder='Choose a role',options=[discord.SelectOption(label=(i.guild.get_role(x).name if i.guild.get_role(x) else 'Missing role')[:90],value=str(x),description=f"{r['spec']['rules']['weights'].get(str(x),1)} ticket(s)") for x in ids])
                        async def amount(j):
                            await j.response.send_modal(TicketNumber(bot,user,cfg,0,int(select.values[0]),r))
                        select.callback=amount;view.add_item(select)
                        return await reply(i,'Edit tickets for this draft only.',view=view)
                    if action=='more':
                        return await reply(i,'Advanced draft controls.',view=Manage(bot,user,r))
                    from .public_ui import panel_embed
                    preview=copy.deepcopy(r)
                    preview['starts']=time.time()+r['spec']['starts_in'];preview['ends']=preview['starts']+r['spec']['duration']
                    embed=panel_embed(preview,cfg,0);embed.set_footer(text='Private preview. Not published.')
                    async def publish(j):
                        await bot.raffles.publish(j.guild,r['id'],j.user.id,draft_revision=r['revision'])
                        from .admin_ui import show_raffle
                        await show_raffle(bot,j,r['id'])
                    tickets='\n'.join(f'<@&{key}>: {value} ticket(s)' for key,value in r['spec']['rules']['weights'].items())
                    await reply(i,'Tickets are added for selected roles only.\n'+tickets,embed=embed,view=Confirm(bot,user,publish,'Publish this raffle'))
                button.callback=run;self.add_item(button)
            mode=discord.ui.Button(label='Results: '+('automatic' if r['result_mode']=='auto' else 'admin approval'))
            async def toggle_mode(i):
                await i.response.defer(ephemeral=True);await authorized(bot,i)
                changed=await bot.db.execute("UPDATE raffles SET result_mode=?,revision=revision+1 WHERE guild=? AND id=? AND revision=? AND status='draft'",('review' if r['result_mode']=='auto' else 'auto',i.guild_id,r['id'],r['revision']))
                if not changed:raise ValueError('This draft changed. Reopen it.')
                from .admin_ui import show_raffle
                await show_raffle(bot,i,r['id'])
            mode.callback=toggle_mode;self.add_item(mode)
        else:
            legacy=Manage(bot,user,r)
            for button in legacy.children:
                allowed=('Refresh','Reroll forfeited') if r['status']=='drawn' else ('Refresh','Resume entries' if r['paused'] else 'Pause entries','End now','Cancel')
                if button.label in allowed:
                    self.add_item(button)
            if r['status']=='drawn' and r.get('entry_role') and r['result_state']=='pending':
                publish=discord.ui.Button(label='Review and publish results',style=discord.ButtonStyle.primary)
                async def results(i):
                    await i.response.defer(ephemeral=True);await authorized(bot,i)
                    winners=[uid for rnd in r['rounds'] for uid in rnd['winners']]
                    async def approve(j):
                        changed=await bot.db.execute("UPDATE raffles SET result_approved=1 WHERE guild=? AND id=? AND status='drawn' AND revision=? AND result_state='pending'",(j.guild_id,r['id'],r['revision']))
                        if not changed:raise ValueError('This result changed. Review it again before publishing.')
                        await bot.db.audit(j.guild_id,j.user.id,'results_approved',f"raffle={r['id']}")
                        await bot.raffles.announce(j.guild,r['id'],j.user.id)
                        await bot.raffles.update_panel(j.guild,r['id'],True)
                        from .admin_ui import show_raffle
                        await show_raffle(bot,j,r['id'])
                    await reply(i,'Winners:\n'+('\n'.join(f'<@{u}>' for u in winners) or 'No eligible winners.')+'\nThe Raffle role will be cleared after publication.',view=Confirm(bot,user,approve,'Publish results'))
                publish.callback=results;self.add_item(publish)
            delivery=discord.ui.Button(label='Prize delivery')
            async def deliver(i):
                await i.response.defer(ephemeral=True);await authorized(bot,i)
                rows=await bot.db.query('SELECT user,status FROM awards WHERE raffle=? ORDER BY round',(r['id'],))
                if not rows:
                    return await reply(i,'No winners have been drawn yet.')
                view=SafeView(bot,user)
                select=discord.ui.Select(placeholder='Choose a winner',options=[discord.SelectOption(label=(i.guild.get_member(x['user']).display_name if i.guild.get_member(x['user']) else 'Former member')[:90],value=str(x['user']),description=x['status']) for x in rows[-25:]])
                async def winner(j):
                    await j.response.send_modal(DeliveryNote(bot,user,r['id'],int(select.values[0])))
                select.callback=winner;view.add_item(select)
                await reply(i,'Record delivery or forfeiture.',view=view)
            delivery.callback=deliver;self.add_item(delivery)
        controls=discord.ui.Button(label='More controls')
        async def more(i):
            await i.response.defer(ephemeral=True);await authorized(bot,i)
            role=i.guild.get_role(r.get('entry_role',0))
            await reply(i,f"Current Raffle role holders: {len(role.members) if role else 0}\nRegistrations and results are retained when roles are cleared.",view=RoleControls(bot,user,r))
        controls.callback=more;self.add_item(controls)
        add_home(self,bot,user)


class RoleControls(SafeView):
    def __init__(self,bot,user,r):
        super().__init__(bot,user)
        if r.get('entry_role'):
            clear=discord.ui.Button(label='Clear Raffle role',style=discord.ButtonStyle.danger)
            async def revoke(i):
                async def run(j):
                    await bot.raffles.clear_entry_role(j.guild,r['id'],j.user.id)
                    from .admin_ui import show_raffle
                    await show_raffle(bot,j,r['id'])
                await reply(i,'Clear the Raffle role from everyone? Open entries will be paused. Registrations and results stay saved.',view=Confirm(bot,user,run,'Clear roles'))
            clear.callback=revoke;self.add_item(clear)
            if r['result_state'] in ('review','sending'):
                recover=discord.ui.Button(label='Recover result announcement')
                async def recovery(i):await i.response.send_modal(RecoverResult(bot,user,r['id']))
                recover.callback=recovery;self.add_item(recover)
                review=discord.ui.Button(label='Find or retry announcement')
                async def check(i):
                    async def run(j):
                        await bot.raffles.review_announcement(j.guild,r['id'],j.user.id)
                        from .admin_ui import show_raffle
                        await show_raffle(bot,j,r['id'])
                    await reply(i,'Read this channel to find the result. If no result message exists, send the saved winners once.',view=Confirm(bot,user,run,'Review announcement'))
                review.callback=check;self.add_item(review)
        members=discord.ui.Button(label='Participants and exports')
        async def people(i):
            await i.response.defer(ephemeral=True)
            await show_participants(bot,i,r['id'])
        members.callback=people;self.add_item(members)
        advanced=discord.ui.Button(label='All management tools')
        async def legacy(i):await reply(i,'All existing raffle controls remain available.',view=Manage(bot,user,r))
        advanced.callback=legacy;self.add_item(advanced)
        add_home(self,bot,user)


async def show_participants(bot,i,rid,offset=0):
    await authorized(bot,i)
    await bot.db.get(i.guild_id,rid)
    rows=await bot.db.query('SELECT user,active FROM entries WHERE raffle=? ORDER BY user LIMIT 21 OFFSET ?',(rid,offset))
    view=SafeView(bot,i.user.id)
    lines=[f"<@{r['user']}> · {'entered' if r['active'] else 'left'}" for r in rows[:20]]
    for label,new_offset in [('Previous',offset-20),('Next',offset+20)]:
        if new_offset<0 or (label=='Next' and len(rows)<=20):continue
        button=discord.ui.Button(label=label)
        async def page(j,new_offset=new_offset):
            await j.response.defer(ephemeral=True)
            await show_participants(bot,j,rid,new_offset)
        button.callback=page;view.add_item(button)
    exclude=discord.ui.UserSelect(placeholder='Exclude a member before closing',min_values=1,max_values=1)
    async def select(j):await j.response.send_modal(ExcludeEntry(bot,i.user.id,rid,exclude.values[0].id))
    exclude.callback=select;view.add_item(exclude)
    for label,kind in [('Export participants','entries'),('Draw proof','proof'),('Management history','history')]:
        button=discord.ui.Button(label=label)
        async def export(j,kind=kind):
            await j.response.defer(ephemeral=True);await authorized(bot,j)
            if kind=='proof':
                value=await bot.db.public_bundle(j.guild_id,rid)
            elif kind=='history':
                value=await bot.db.query('SELECT actor,action,detail,at FROM audit WHERE guild=? ORDER BY id DESC LIMIT 1000',(j.guild_id,))
            else:
                rows=await bot.db.query("SELECT e.user,e.joined,e.active,COALESCE(a.status,'') AS award_status FROM entries e LEFT JOIN awards a ON a.raffle=e.raffle AND a.user=e.user WHERE e.raffle=? ORDER BY e.user",(rid,))
                stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=['user','joined','active','award_status']);writer.writeheader();writer.writerows(rows)
                return await reply(j,'Participant export.',file=attachment(f'raffle-{rid}-entries.csv',stream.getvalue().encode('utf-8-sig')))
            await reply(j,'Saved records.',file=attachment(f'raffle-{rid}-{kind}.json',value))
        button.callback=export;view.add_item(button)
    add_home(view,bot,i.user.id)
    await reply(i,'\n'.join(lines) or 'No participants yet.',view=view)


class ExcludeEntry(SafeModal):
    def __init__(self,bot,user,rid,member):
        super().__init__(bot,user,'Exclude a participant')
        self.rid,self.member=rid,member
        self.reason=discord.ui.TextInput(label='Reason for exclusion',max_length=500)
        self.add_item(self.reason)
    async def on_submit(self,i):
        await i.response.defer(ephemeral=True);await self.check(i)
        async def run(j):
            await self.bot.db.exclude(j.guild_id,self.rid,self.member,j.user.id,self.reason.value)
            await show_participants(self.bot,j,self.rid)
        await reply(i,f'Exclude <@{self.member}> from this raffle?',view=Confirm(self.bot,self.user,run,'Exclude participant'))


class RecoverResult(SafeModal):
    def __init__(self,bot,user,rid):
        super().__init__(bot,user,'Recover an existing result')
        self.rid=rid
        self.message=discord.ui.TextInput(label='Result message link or message ID',max_length=250)
        self.add_item(self.message)
    async def on_submit(self,i):
        await i.response.defer(ephemeral=True);await self.check(i)
        raw=self.message.value.rstrip('/').split('/')[-1]
        if not raw.isdecimal():raise ValueError('Paste the result message link.')
        await self.bot.raffles.recover_announcement(i.guild,self.rid,int(raw),i.user.id)
        await self.bot.raffles.update_panel(i.guild,self.rid,True)
        from .admin_ui import show_raffle
        await show_raffle(self.bot,i,self.rid)


class DeliveryNote(SafeModal):
    def __init__(self,bot,user,rid,winner):
        super().__init__(bot,user,'Record prize delivery')
        self.rid,self.winner=rid,winner
        self.state=discord.ui.TextInput(label='delivered or forfeited',default='delivered',max_length=10)
        self.note=discord.ui.TextInput(label='Short note',max_length=500)
        self.add_item(self.state);self.add_item(self.note)
    async def on_submit(self,i):
        await i.response.defer(ephemeral=True);await self.check(i)
        status=self.state.value.strip().lower()
        async def save(j):
            await self.bot.db.award_status(j.guild_id,self.rid,self.winner,status,j.user.id,self.note.value)
            from .admin_ui import show_raffle
            await show_raffle(self.bot,j,self.rid)
        await reply(i,f'Record this prize as {status}?',view=Confirm(self.bot,self.user,save,'Save delivery'))
