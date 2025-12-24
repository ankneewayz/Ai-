const canvas=document.createElement('canvas');
canvas.style.position='fixed';
canvas.style.top=0;
canvas.style.left=0;
canvas.style.width='100%';
canvas.style.height='100%';
canvas.style.zIndex='-1';
canvas.style.pointerEvents='none';
document.body.appendChild(canvas);

const ctx=canvas.getContext('2d');
let width=canvas.width=window.innerWidth;
let height=canvas.height=window.innerHeight;
window.addEventListener('resize',()=>{width=canvas.width=window.innerWidth;height=canvas.height=window.innerHeight;});

class Particle{
  constructor(){
    this.x=Math.random()*width;
    this.y=Math.random()*height;
    this.size=Math.random()*3+1;
    this.speedX=Math.random()*0.5-0.25;
    this.speedY=Math.random()*0.5-0.25;
    this.baseColor=['#00ffff','#ff69b4','#ff00ff'][Math.floor(Math.random()*3)];
  }
  draw(){
    ctx.beginPath();
    ctx.fillStyle=this.baseColor;
    ctx.shadowColor=this.baseColor;
    ctx.shadowBlur=8;
    ctx.arc(this.x,this.y,this.size,0,Math.PI*2);
    ctx.fill();
  }
  update(mouse){
    this.x+=this.speedX;
    this.y+=this.speedY;
    if(this.x<0||this.x>width) this.speedX*=-1;
    if(this.y<0||this.y>height) this.speedY*=-1;
    if(mouse){
      const dx=mouse.x-this.x;
      const dy=mouse.y-this.y;
      const dist=Math.sqrt(dx*dx+dy*dy);
      if(dist<100){
        const angle=Math.atan2(dy,dx);
        this.x-=Math.cos(angle)*0.5;
        this.y-=Math.sin(angle)*0.5;
      }
    }
    this.draw();
  }
}

const particlesArray=[];
const numberOfParticles=80;
for(let i=0;i<numberOfParticles;i++) particlesArray.push(new Particle());

let mouse=null;
document.addEventListener('mousemove',e=>{mouse={x:e.clientX,y:e.clientY};});
document.addEventListener('mouseleave',()=>mouse=null);

function animate(){
  ctx.clearRect(0,0,width,height);
  particlesArray.forEach(p=>p.update(mouse));
  requestAnimationFrame(animate);
}
animate();